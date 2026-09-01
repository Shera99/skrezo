"""Сборка клиента Cloud API из конфигурации (боевой либо лог-фолбэк)."""

from __future__ import annotations

from app.core.config import settings
from app.shared.whatsapp_cloud.client import WhatsAppCloudClient
from app.shared.whatsapp_cloud.graph_client import GraphWhatsAppClient
from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient


def build_whatsapp_client() -> WhatsAppCloudClient:
    """Боевой клиент при заполненном токене, иначе лог-фолбэк.

    Включение реальной отправки — заполнение ``WA_CLOUD_ACCESS_TOKEN`` без
    правок кода (та же дисциплина, что у FCM/APNs в ``shared/push``).
    """
    if settings.wa_cloud_access_token:
        return GraphWhatsAppClient(settings.wa_cloud_api_url, settings.wa_cloud_access_token)
    return LoggingWhatsAppClient()
