"""Канал «в лог» — для разработки и тестов.

Работает, когда провайдеры не настроены (``OTP_CHANNELS`` пуст): код виден в
логе, а при ``OTP_EXPOSE_CODE`` возвращается в ответе. Это и есть прежнее
поведение заглушки — оно осталось дефолтом, чтобы локальная разработка не
требовала боевых токенов.

⚠️ В проде так выпускать нельзя: код в логах — это код у всех, у кого есть
доступ к логам. Поэтому на старте приложение предупреждает, если каналы не
настроены (см. ``build_channels``).
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


class LoggingChannel:
    """Пишет код в лог вместо отправки."""

    name = "log"

    async def send(self, phone: str, code: str) -> None:
        logger.info("otp_code_issued", phone=phone, code=code)
