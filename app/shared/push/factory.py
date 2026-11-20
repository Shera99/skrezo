"""Сборка транспорта пушей из настроек — композиционный корень.

Config-driven: ``PUSH_ENABLED=true`` + заполненные креды включают живые каналы
(FCM — Android и iOS-токены Firebase, APNs — «сырые» iOS-токены) без правки
кода; чего не хватает — те устройства уходят в лог-фолбэк, и система работает
дальше. Кеш на процесс:
каналы держат долгоживущие httpx-клиенты и кеш auth-токенов.
"""

from __future__ import annotations

from functools import lru_cache

import structlog

from app.core.config import settings
from app.shared.push.apns import ApnsChannel
from app.shared.push.channels import PushChannel
from app.shared.push.fcm import FcmChannel
from app.shared.push.logging_channel import LoggingPushChannel
from app.shared.push.transport import APNS, FCM, PushTransport

logger = structlog.get_logger(__name__)


@lru_cache
def build_push_transport() -> PushTransport:
    """Собрать транспорт: живые каналы по кредам, остальное — в лог.

    Канал FCM обслуживает Android и iPhone с токеном Firebase, канал APNs —
    iPhone с «сырым» device token APNs (маршрут — :func:`provider_for`).
    """
    channels: dict[str, PushChannel] = {}
    if settings.push_enabled:
        if settings.fcm_service_account_json:
            try:
                channels[FCM] = FcmChannel(settings.fcm_service_account_json)
            except Exception as exc:  # битый JSON/ключ не должен ронять процесс
                logger.error("fcm_channel_init_failed", error=str(exc))
        if (
            settings.apns_key
            and settings.apns_key_id
            and settings.apns_team_id
            and settings.apns_bundle_id
        ):
            try:
                channels[APNS] = ApnsChannel(
                    settings.apns_key,
                    settings.apns_key_id,
                    settings.apns_team_id,
                    settings.apns_bundle_id,
                    use_sandbox=settings.apns_use_sandbox,
                )
            except Exception as exc:
                logger.error("apns_channel_init_failed", error=str(exc))
    logger.info(
        "push_transport_built",
        fcm=(FCM if FCM in channels else "log"),
        apns=(APNS if APNS in channels else "log"),
    )
    return PushTransport(channels=channels, fallback=LoggingPushChannel())
