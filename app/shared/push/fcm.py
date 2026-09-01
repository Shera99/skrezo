"""Канал FCM (HTTP v1) — доставка пушей на Android.

Авторизация — OAuth2 сервис-аккаунтом: подписываем JWT (RS256) приватным
ключом из JSON и меняем его на access token, который кешируем до истечения.
Google-SDK не тащим: нужен ровно один grant и один POST, а зависимость
``google-auth`` тянет полмира ради этого.

``NotRegistered``/``UNREGISTERED`` → :class:`DeadTokenError` — токен снимается
без ретраев. Остальные ошибки — :class:`PushDeliveryError` (можно повторить).
"""

from __future__ import annotations

import json
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
_OAUTH_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
#: Обновлять access token заранее, а не в момент истечения.
_TOKEN_SLACK_SECONDS = 60


def load_service_account(value: str) -> dict[str, Any]:
    """Настройка — сам JSON либо путь к файлу с ним (решаем по первому символу)."""
    raw = value.strip()
    if not raw.startswith("{"):
        raw = Path(raw).read_text()
    data: dict[str, Any] = json.loads(raw)
    return data


class FcmChannel:
    """Канал FCM HTTP v1 поверх httpx."""

    name = "fcm"

    def __init__(
        self, service_account_json: str, client: httpx.AsyncClient | None = None
    ) -> None:
        account = load_service_account(service_account_json)
        self._project_id: str = account["project_id"]
        self._client_email: str = account["client_email"]
        self._private_key: str = account["private_key"]
        self._token_uri: str = account.get("token_uri", "https://oauth2.googleapis.com/token")
        # Клиент долгоживущий: воркер шлёт пачками, соединение переиспользуется.
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)
        self._access_token: str = ""
        self._token_expires_at: float = 0.0

    async def _token(self) -> str:
        """Access token c кешем; новый — по JWT-grant'у сервис-аккаунта."""
        if self._access_token and time.time() < self._token_expires_at - _TOKEN_SLACK_SECONDS:
            return self._access_token
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": self._client_email,
                "scope": _OAUTH_SCOPE,
                "aud": self._token_uri,
                "iat": now,
                "exp": now + 3600,
            },
            self._private_key,
            algorithm="RS256",
        )
        try:
            resp = await self._client.post(
                self._token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
            )
        except httpx.HTTPError as exc:
            raise PushDeliveryError(f"FCM oauth: {exc}") from exc
        if resp.status_code >= 400:
            raise PushDeliveryError(f"FCM oauth: HTTP {resp.status_code}")
        data = resp.json()
        self._access_token = str(data["access_token"])
        self._token_expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    def _build_message(self, token: str, message: PushMessage) -> dict[str, Any]:
        android: dict[str, Any] = {
            # ВСЕГДА HIGH: Doze копит NORMAL-приоритет до окна обслуживания
            # (§7 «иначе Doze копит»), а «неважных» пушей у нас не бывает —
            # low не шлётся вовсе, важность решает канал, не приоритет доставки.
            "priority": "HIGH",
        }
        if message.collapse_key:
            android["collapse_key"] = message.collapse_key
        if message.ttl_seconds is not None:
            android["ttl"] = f"{max(message.ttl_seconds, 0)}s"
        body: dict[str, Any] = {"token": token, "data": message.data, "android": android}
        if not message.is_silent:
            body["notification"] = {"title": message.title, "body": message.body or ""}
            android_notification: dict[str, Any] = {"channel_id": message.android_channel}
            if message.badge is not None:
                android_notification["notification_count"] = message.badge
            android["notification"] = android_notification
        return {"message": body}

    async def send(self, token: str, message: PushMessage) -> None:
        access_token = await self._token()
        url = f"https://fcm.googleapis.com/v1/projects/{self._project_id}/messages:send"
        try:
            resp = await self._client.post(
                url,
                json=self._build_message(token, message),
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise PushDeliveryError(f"FCM: {exc}") from exc
        if resp.status_code < 400:
            return
        text = resp.text[:500]
        # 404/UNREGISTERED — токен мёртв; INVALID_ARGUMENT про токен — тоже он
        # (протухшие токены FCM отдаёт обоими способами).
        if resp.status_code == 404 or "UNREGISTERED" in text:
            raise DeadTokenError(f"FCM: {resp.status_code}")
        if resp.status_code == 400 and "registration token" in text.lower():
            raise DeadTokenError("FCM: invalid registration token")
        logger.warning("fcm_send_failed", status=resp.status_code, body=text)
        raise PushDeliveryError(f"FCM: HTTP {resp.status_code}")
