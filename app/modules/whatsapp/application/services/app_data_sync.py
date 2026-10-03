"""Запрос данных приложения WhatsApp Business: контакты и история переписки.

Meta разрешает каждый запрос один раз за подключение и только в первые 24 часа
после него: не успели — клинике придётся подключаться заново. Поэтому запрос
делается сразу при подключении, а ежечасная проверка номеров повторяет то, что
не прошло, пока окно открыто.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import structlog

from app.modules.whatsapp.application.services.credentials import SELF_SERVICE, binding_token
from app.shared.db.enums import WhatsAppMode
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.whatsapp_cloud import (
    SYNC_CONTACTS,
    SYNC_HISTORY,
    MetaOnboardingClient,
    MetaOnboardingError,
)

logger = structlog.get_logger(__name__)

#: Окно Meta на синхронизацию после подключения номера.
SYNC_WINDOW = timedelta(hours=24)


async def request_app_data(
    binding: ClinicWhatsApp, onboarding: MetaOnboardingClient, now: datetime
) -> int:
    """Запросить ещё не запрошенное; вернуть число успешных запросов.

    Только номер, который работает и в приложении (coexistence), подключён самой
    клиникой и не позже 24 часов назад. Отказ Meta не роняет вызывающего: шаг
    останется незапрошенным и повторится через час.
    """
    if (
        not binding.active
        or binding.mode is not WhatsAppMode.COEXISTENCE
        or binding.connected_via != SELF_SERVICE
        or binding.connected_at is None
        or now - binding.connected_at > SYNC_WINDOW
    ):
        return 0
    token = binding_token(binding)
    if token is None:
        return 0
    done = 0
    for sync_type in (SYNC_CONTACTS, SYNC_HISTORY):
        requested = (
            binding.contacts_sync_requested_at
            if sync_type == SYNC_CONTACTS
            else binding.history_sync_requested_at
        )
        if requested is not None:
            continue
        try:
            await onboarding.request_sync(binding.phone_number_id, token, sync_type)
        except MetaOnboardingError as exc:
            logger.warning(
                "wa_app_sync_request_failed",
                clinic_id=str(binding.clinic_id),
                sync_type=sync_type,
                error=str(exc),
            )
            continue
        if sync_type == SYNC_CONTACTS:
            binding.contacts_sync_requested_at = now
        else:
            binding.history_sync_requested_at = now
        done += 1
    return done
