"""Транспорт WhatsApp Cloud API (Meta Graph API).

НЕ путать с ``app/shared/notifications`` — там каналы доставки OTP/текста на
телефон (в том числе «whatsapp» стороннего провайдера). Здесь — официальный
Cloud API Meta: переписка клиники с пациентами (модуль ``whatsapp``).
"""

from __future__ import annotations

from app.shared.whatsapp_cloud.client import (
    DownloadedMedia,
    SentMessage,
    WhatsAppCloudClient,
    WhatsAppSendError,
)
from app.shared.whatsapp_cloud.factory import build_whatsapp_client

__all__ = [
    "DownloadedMedia",
    "SentMessage",
    "WhatsAppCloudClient",
    "WhatsAppSendError",
    "build_whatsapp_client",
]
