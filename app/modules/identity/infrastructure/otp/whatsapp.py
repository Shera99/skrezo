"""Доставка кода через WhatsApp-бота (перенесено из showgo_api).

API принимает номер **без ведущего плюса** и текст сообщения, а результат
отдаёт полем ``success``: HTTP 200 с ``success: false`` — это недоставка.

⚠️ В исходнике запрос шёл с ``verify=False`` (проверка TLS-сертификата
отключена). Сюда это НЕ перенесено: с выключенной проверкой номер телефона и
код уходят тому, кто перехватил соединение. Если у провайдера действительно
самоподписанный сертификат — правильное решение положить его CA в доверенные,
а не отключать проверку всем запросам.
"""

from __future__ import annotations

import httpx
import structlog

from app.core.config import settings
from app.modules.identity.domain.otp_message import whatsapp_text
from app.modules.identity.infrastructure.otp.channels import OtpDeliveryError

logger = structlog.get_logger(__name__)


class WhatsAppChannel:
    """Канал WhatsApp (бот-шлюз с ``accountId``)."""

    name = "whatsapp"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def send(self, phone: str, code: str) -> None:
        payload = {
            "accountId": settings.whatsapp_account_id,
            "to": phone.lstrip("+"),
            "message": whatsapp_text(code),
        }

        try:
            response = await self._post(payload)
        except httpx.HTTPError as exc:
            raise OtpDeliveryError(f"whatsapp: сеть недоступна: {exc}") from exc

        if response.status_code != httpx.codes.OK:
            raise OtpDeliveryError(f"whatsapp: HTTP {response.status_code}")

        body = response.json()
        if not body.get("success"):
            raise OtpDeliveryError(f"whatsapp: {body.get('message', 'not delivered')}")

    async def _post(self, payload: dict[str, str]) -> httpx.Response:
        timeout = settings.otp_delivery_timeout_seconds
        url = settings.whatsapp_api_url
        headers = {"Content-Type": "application/json"}
        if self._client is not None:
            return await self._client.post(url, json=payload, headers=headers, timeout=timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, json=payload, headers=headers)
