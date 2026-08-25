"""Сборка транспорта пушей из настроек — композиционный корень.

Кредов FCM/APNs пока нет (см. ``docs/PUSH_NOTIFICATIONS.md`` §3.4 «лог-фолбэк»):
при пустом конфиге весь транспорт — :class:`LoggingPushChannel` (пуш виден в
логе, ничего не падает). Реальные каналы FCM/APNs подключаются здесь же, когда
появятся ключи — до тех пор их код не грузится, чтобы не тащить в приложение
непроверенную сетевую логику и новую крипто-зависимость (RS256 для FCM, ES256
для APNs) без песочницы, где её можно проверить.
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.core.config import settings
from app.shared.push.logging_channel import LoggingPushChannel
from app.shared.push.transport import PushTransport

logger = structlog.get_logger(__name__)


@lru_cache
def build_push_transport() -> PushTransport:
    """Собрать транспорт пушей; без кредов — всё в лог (кешируется на процесс).

    TODO(creds): при заполненных ``fcm_*``/``apns_*`` собрать ``FcmChannel`` и
    ``ApnsChannel`` и передать ``channels={"android": fcm, "ios": apns}``.
    Реализация каналов — отдельным инкрементом, когда появятся ключи и песочница
    для проверки живой доставки.
    """
    if settings.push_enabled:
        # Креды заявлены, но живые каналы ещё не реализованы — честно предупреждаем,
        # что доставка пока идёт в лог, а не в FCM/APNs.
        logger.warning("push_transport_stub", reason="fcm/apns channels not implemented yet")
    else:
        logger.info("push_transport_logging", reason="push disabled or creds absent")
    return PushTransport(channels={}, fallback=LoggingPushChannel())
