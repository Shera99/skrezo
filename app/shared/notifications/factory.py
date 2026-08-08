"""Сборка каналов уведомлений из настроек — композиционный корень.

Транспорты сами настроек не читают: адреса, логины и таймаут приходят сюда, а
дальше передаются в конструкторы. Из-за этого их можно собрать в тесте с чужим
адресом, не подменяя глобальные ``settings``.

``NOTIFY_CHANNELS`` задаёт каналы **в порядке приоритета** (``whatsapp,sms``):
сначала бесплатный WhatsApp, платная SMS — запасным. Канал без обязательных
настроек в цепочку не попадает и говорит об этом в лог на старте: держать в
очереди заведомо мёртвого провайдера — значит тратить на него таймаут.
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.core.config import settings
from app.shared.notifications.channels import ChatChannel, TextChannel
from app.shared.notifications.logging_channel import LoggingTextChannel
from app.shared.notifications.smspro import SmsProTextChannel
from app.shared.notifications.telegram_chat import TelegramChatChannel
from app.shared.notifications.whatsapp import WhatsAppTextChannel

logger = structlog.get_logger(__name__)


def _build_whatsapp() -> TextChannel | None:
    if not (settings.whatsapp_api_url and settings.whatsapp_account_id):
        return None
    return WhatsAppTextChannel(
        api_url=settings.whatsapp_api_url,
        account_id=settings.whatsapp_account_id,
        timeout=settings.notify_delivery_timeout_seconds,
    )


def _build_sms() -> TextChannel | None:
    if not (
        settings.smspro_url
        and settings.smspro_login
        and settings.smspro_password
        and settings.smspro_sender
    ):
        return None
    return SmsProTextChannel(
        url=settings.smspro_url,
        login=settings.smspro_login,
        password=settings.smspro_password,
        sender=settings.smspro_sender,
        timeout=settings.notify_delivery_timeout_seconds,
    )


@lru_cache
def build_text_channels() -> tuple[TextChannel, ...]:
    """Собрать каналы по ``NOTIFY_CHANNELS``; ни одного настроенного — канал в лог.

    Кешируется на процесс: сборка читает настройки и пишет предупреждения, а от
    запроса не зависит — повторять её на каждый вызов значит засорять лог.
    """
    builders = {"whatsapp": _build_whatsapp, "sms": _build_sms}

    channels: list[TextChannel] = []
    for name in settings.notify_channels:
        builder = builders.get(name)
        if builder is None:
            logger.warning("notify_channel_unknown", channel=name)
            continue
        channel = builder()
        if channel is None:
            logger.warning("notify_channel_not_configured", channel=name)
            continue
        channels.append(channel)

    if not channels:
        logger.warning("notify_delivery_not_configured", production=settings.is_production)
        return (LoggingTextChannel(),)
    return tuple(channels)


@lru_cache
def build_team_chat() -> ChatChannel | None:
    """Канал в чат команды Skrezo; не настроен — ``None`` (события идут в лог)."""
    if not (settings.telegram_team_bot_token and settings.telegram_team_chat_id):
        logger.warning("team_chat_not_configured", production=settings.is_production)
        return None
    return TelegramChatChannel(
        bot_token=settings.telegram_team_bot_token,
        chat_id=settings.telegram_team_chat_id,
        timeout=settings.notify_delivery_timeout_seconds,
    )
