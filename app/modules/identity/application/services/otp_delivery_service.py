"""Доставка OTP-кода соло-врачу.

v1 — заглушка (SMS/WhatsApp-провайдер КГ не выбран): факт отправки логируется.
Реальная интеграция подключается здесь одним методом, не трогая флоу входа.
Именно отсутствие канала доставки в своё время заставило убрать OTP из этого
контура; сейчас флоу восстановлен, но в прод без провайдера выпускать нельзя.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


class OtpDeliveryService:
    """Отправка кода подтверждения на телефон (пока — заглушка)."""

    async def send_code(self, phone: str, code: str) -> None:
        # TODO(провайдер КГ): отправка SMS/WhatsApp с кодом на ``phone``.
        logger.info("otp_code_issued", phone=phone, code_len=len(code))
