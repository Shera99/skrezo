"""Сборка цепочки каналов доставки OTP из настроек.

``OTP_CHANNELS`` задаёт каналы **в порядке приоритета**: `telegram,whatsapp,sms`
— сначала бесплатный Telegram, потом WhatsApp, и только потом платная SMS. Это
и есть смысл списка: у номера может не быть Telegram, и тогда попытка стоит
ничего, а перейти к следующему каналу нужно автоматически.

Канал, у которого не заполнены обязательные настройки, в цепочку не попадает и
пишет об этом в лог на старте: молча подставлять его в очередь — значит на
каждом входе тратить таймаут на заведомо мёртвый провайдер.
"""

from __future__ import annotations

import structlog

from app.core.config import settings
from app.modules.identity.infrastructure.otp.channels import OtpChannel
from app.modules.identity.infrastructure.otp.logging_channel import LoggingChannel
from app.modules.identity.infrastructure.otp.smspro import SmsProChannel
from app.modules.identity.infrastructure.otp.telegram_gateway import TelegramGatewayChannel
from app.modules.identity.infrastructure.otp.whatsapp import WhatsAppChannel

logger = structlog.get_logger(__name__)


def _telegram_ready() -> bool:
    return bool(settings.telegram_gateway_url and settings.telegram_gateway_token)


def _whatsapp_ready() -> bool:
    return bool(settings.whatsapp_api_url and settings.whatsapp_account_id)


def _smspro_ready() -> bool:
    return bool(
        settings.smspro_url
        and settings.smspro_login
        and settings.smspro_password
        and settings.smspro_sender
    )


def build_channels() -> list[OtpChannel]:
    """Собрать каналы по ``OTP_CHANNELS``; ни одного настроенного — канал в лог."""
    builders: dict[str, tuple[bool, type[OtpChannel]]] = {
        "telegram": (_telegram_ready(), TelegramGatewayChannel),
        "whatsapp": (_whatsapp_ready(), WhatsAppChannel),
        "sms": (_smspro_ready(), SmsProChannel),
    }

    channels: list[OtpChannel] = []
    for name in settings.otp_channels:
        entry = builders.get(name)
        if entry is None:
            logger.warning("otp_channel_unknown", channel=name)
            continue
        ready, channel_cls = entry
        if not ready:
            logger.warning("otp_channel_not_configured", channel=name)
            continue
        channels.append(channel_cls())

    if not channels:
        # Прод без канала доставки — это вход, который не работает: код уходит
        # только в лог. Предупреждаем громко, но не падаем: локальная разработка
        # и тесты живут именно на этом режиме.
        logger.warning("otp_delivery_not_configured", production=settings.is_production)
        return [LoggingChannel()]
    return channels
