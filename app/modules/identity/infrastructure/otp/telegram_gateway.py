"""Доставка кода через Telegram Gateway (перенесено из showgo_api).

Gateway сам показывает пользователю код в Telegram по своему шаблону, поэтому
текст мы не передаём — только сам код и его срок жизни. Это же делает канал
самым дешёвым: сообщение не уходит в сеть оператора.

Ответ провайдера — ``{"ok": true, "result": {...}}`` либо ``{"ok": false,
"error": "..."}``: HTTP 200 ещё не значит доставку, поэтому проверяется тело.
"""

from __future__ import annotations

import httpx
import structlog

from app.core.config import settings
from app.modules.identity.infrastructure.otp.channels import OtpDeliveryError

logger = structlog.get_logger(__name__)


class TelegramGatewayChannel:
    """Канал Telegram Gateway (``sendVerificationMessage``)."""

    name = "telegram"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def send(self, phone: str, code: str) -> None:
        url = f"{settings.telegram_gateway_url.rstrip('/')}/sendVerificationMessage"
        payload = {
            "phone_number": phone,
            "code": code,
            # ttl провайдера держим равным нашему сроку жизни кода: показывать
            # код дольше, чем он действует, — только путать пользователя.
            "ttl": settings.otp_ttl_seconds,
        }
        headers = {
            "Authorization": f"Bearer {settings.telegram_gateway_token}",
            "Content-Type": "application/json",
        }

        try:
            response = await self._post(url, payload, headers)
        except httpx.HTTPError as exc:
            raise OtpDeliveryError(f"telegram: сеть недоступна: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise OtpDeliveryError(f"telegram: HTTP {response.status_code}")

        body = response.json()
        if not body.get("ok"):
            # Частый случай — у номера нет Telegram: это не сбой, а повод
            # перейти к следующему каналу.
            raise OtpDeliveryError(f"telegram: {body.get('error', 'unknown error')}")

    async def _post(
        self, url: str, payload: dict[str, object], headers: dict[str, str]
    ) -> httpx.Response:
        timeout = settings.otp_delivery_timeout_seconds
        if self._client is not None:
            return await self._client.post(url, json=payload, headers=headers, timeout=timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, json=payload, headers=headers)
