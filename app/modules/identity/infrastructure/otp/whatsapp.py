"""Доставка кода через WhatsApp-бота.

Сам запрос к провайдеру живёт в общем транспорте
(:mod:`app.shared.notifications.whatsapp`) — тот же шлюз носит и уведомления.
Здесь остаётся то, что относится именно к OTP: текст сообщения по назначению
кода и своя ошибка недоставки, на которую смотрит цепочка каналов.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.modules.identity.domain.models import OtpPurpose
from app.modules.identity.domain.otp_message import whatsapp_text
from app.modules.identity.infrastructure.otp.channels import OtpDeliveryError
from app.shared.notifications.channels import MessageDeliveryError
from app.shared.notifications.whatsapp import WhatsAppTextChannel


class WhatsAppChannel:
    """Канал WhatsApp (бот-шлюз с ``accountId``)."""

    name = "whatsapp"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._transport = WhatsAppTextChannel(
            api_url=settings.whatsapp_api_url,
            account_id=settings.whatsapp_account_id,
            timeout=settings.otp_delivery_timeout_seconds,
            client=client,
        )

    async def send(self, phone: str, code: str, purpose: OtpPurpose = OtpPurpose.LOGIN) -> None:
        try:
            await self._transport.send_text(phone, whatsapp_text(code, purpose))
        except MessageDeliveryError as exc:
            raise OtpDeliveryError(str(exc)) from exc
