"""Доставка кода по SMS через SMSPRO (nikita.kg).

XML-протокол провайдера живёт в общем транспорте
(:mod:`app.shared.notifications.smspro`): тем же каналом уходят уведомления, и
разбор ответа должен быть один на всех. Здесь — текст кода и ошибка недоставки,
на которую смотрит цепочка каналов OTP.
"""

from __future__ import annotations

import httpx

from app.core.config import settings
from app.modules.identity.domain.models import OtpPurpose
from app.modules.identity.domain.otp_message import sms_text
from app.modules.identity.infrastructure.otp.channels import OtpDeliveryError
from app.shared.notifications.channels import MessageDeliveryError
from app.shared.notifications.smspro import SmsProTextChannel


class SmsProChannel:
    """Канал SMS через SMSPRO (nikita.kg)."""

    name = "sms"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._transport = SmsProTextChannel(
            url=settings.smspro_url,
            login=settings.smspro_login,
            password=settings.smspro_password,
            sender=settings.smspro_sender,
            timeout=settings.otp_delivery_timeout_seconds,
            client=client,
        )

    async def send(self, phone: str, code: str, purpose: OtpPurpose = OtpPurpose.LOGIN) -> None:
        try:
            await self._transport.send_text(phone, sms_text(code, purpose))
        except MessageDeliveryError as exc:
            raise OtpDeliveryError(str(exc)) from exc
