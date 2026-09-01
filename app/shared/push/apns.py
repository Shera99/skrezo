"""Канал APNs (token-based) — доставка пушей на iOS.

Авторизация — JWT ES256 ключом ``.p8`` (key id + team id); Apple требует
обновлять его раз в 20–60 минут — кешируем на 50. Соединение HTTP/2 (это
требование APNs) и долгоживущее: рукопожатие дорогое, воркер шлёт пачками.

``410 Unregistered`` / ``BadDeviceToken`` → :class:`DeadTokenError`; остальное —
:class:`PushDeliveryError` (можно повторить).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import jwt
import structlog

from app.shared.push.channels import DeadTokenError, PushDeliveryError
from app.shared.push.message import PushMessage

logger = structlog.get_logger(__name__)

_TIMEOUT = 10.0
#: Пересоздавать auth-JWT каждые 50 минут (правило Apple: 20–60).
_JWT_TTL_SECONDS = 50 * 60
#: apns-collapse-id ограничен 64 байтами — длиннее APNs отвергает весь пуш.
_COLLAPSE_LIMIT = 64


def load_key(value: str) -> str:
    """Настройка — содержимое ``.p8`` либо путь к файлу; ``\\n`` — перевод строки."""
    raw = value.strip()
    if not raw.startswith("-----"):
        return Path(raw).read_text()
    return raw.replace("\\n", "\n")


class ApnsChannel:
    """Канал APNs поверх httpx (HTTP/2)."""

    name = "apns"

    def __init__(
        self,
        key: str,
        key_id: str,
        team_id: str,
        bundle_id: str,
        *,
        use_sandbox: bool,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._key = load_key(key)
        self._key_id = key_id
        self._team_id = team_id
        self._bundle_id = bundle_id
        host = "api.sandbox.push.apple.com" if use_sandbox else "api.push.apple.com"
        self._base = f"https://{host}"
        self._client = client or httpx.AsyncClient(http2=True, timeout=_TIMEOUT)
        self._jwt: str = ""
        self._jwt_issued_at: float = 0.0

    def _auth_token(self) -> str:
        if self._jwt and time.time() - self._jwt_issued_at < _JWT_TTL_SECONDS:
            return self._jwt
        now = int(time.time())
        self._jwt = jwt.encode(
            {"iss": self._team_id, "iat": now},
            self._key,
            algorithm="ES256",
            headers={"kid": self._key_id},
        )
        self._jwt_issued_at = float(now)
        return self._jwt

    def _headers(self, message: PushMessage) -> dict[str, str]:
        headers = {
            "authorization": f"bearer {self._auth_token()}",
            "apns-topic": self._bundle_id,
            "apns-push-type": "alert",
            # 10 — немедленно; 5 — с учётом энергосбережения устройства.
            "apns-priority": "10" if message.urgent else "5",
        }
        if message.collapse_key:
            headers["apns-collapse-id"] = message.collapse_key[:_COLLAPSE_LIMIT]
        if message.ttl_seconds is not None:
            headers["apns-expiration"] = str(int(time.time()) + max(message.ttl_seconds, 0))
        return headers

    def _body(self, message: PushMessage) -> dict[str, Any]:
        aps: dict[str, Any] = {}
        if not message.is_silent:
            aps["alert"] = {"title": message.title, "body": message.body or ""}
            aps["sound"] = "default"
        if message.badge is not None:
            aps["badge"] = message.badge
        if message.thread_id:
            aps["thread-id"] = message.thread_id
        if message.urgent:
            # Требует entitlement time-sensitive у приложения; без него Apple
            # молча понизит до active — деградация, а не отказ.
            aps["interruption-level"] = "time-sensitive"
        if message.ios_category:
            aps["category"] = message.ios_category
        # data — на верхнем уровне payload'а, рядом с aps (правило APNs).
        return {"aps": aps, **message.data}

    async def send(self, token: str, message: PushMessage) -> None:
        try:
            resp = await self._client.post(
                f"{self._base}/3/device/{token}",
                json=self._body(message),
                headers=self._headers(message),
            )
        except httpx.HTTPError as exc:
            raise PushDeliveryError(f"APNs: {exc}") from exc
        if resp.status_code < 400:
            return
        text = resp.text[:300]
        if resp.status_code == 410 or "BadDeviceToken" in text or "Unregistered" in text:
            raise DeadTokenError(f"APNs: {resp.status_code}")
        logger.warning("apns_send_failed", status=resp.status_code, body=text)
        raise PushDeliveryError(f"APNs: HTTP {resp.status_code}")
