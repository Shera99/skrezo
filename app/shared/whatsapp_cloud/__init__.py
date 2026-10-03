"""Транспорт WhatsApp Cloud API (Meta Graph API).

НЕ путать с ``app/shared/notifications`` — там каналы доставки OTP/текста на
телефон (в том числе «whatsapp» стороннего провайдера). Здесь — официальный
Cloud API Meta: переписка клиники с пациентами (модуль ``whatsapp``).
"""

from __future__ import annotations

from app.shared.whatsapp_cloud.client import (
    DownloadedMedia,
    SentMessage,
    TemplateInfo,
    WhatsAppCloudClient,
    WhatsAppSendError,
)
from app.shared.whatsapp_cloud.factory import build_onboarding_client, build_whatsapp_client
from app.shared.whatsapp_cloud.onboarding import (
    SYNC_CONTACTS,
    SYNC_HISTORY,
    MetaOnboardingClient,
    MetaOnboardingError,
    PhoneNumberInfo,
)

__all__ = [
    "SYNC_CONTACTS",
    "SYNC_HISTORY",
    "DownloadedMedia",
    "MetaOnboardingClient",
    "MetaOnboardingError",
    "PhoneNumberInfo",
    "SentMessage",
    "TemplateInfo",
    "WhatsAppCloudClient",
    "WhatsAppSendError",
    "build_onboarding_client",
    "build_whatsapp_client",
]
