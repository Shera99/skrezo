"""Сборка клиента Cloud API из конфигурации (боевой либо лог-фолбэк).

Ф8: клиент один на процесс — у него один пул соединений к Graph API; новый
экземпляр на каждый вызов означал TLS-рукопожатие на каждое сообщение.
"""

from __future__ import annotations

from app.core.config import settings
from app.shared.http import shared_async_client
from app.shared.whatsapp_cloud.client import WhatsAppCloudClient
from app.shared.whatsapp_cloud.graph_client import GraphWhatsAppClient
from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient
from app.shared.whatsapp_cloud.onboarding import GraphOnboardingClient, MetaOnboardingClient

#: Таймаут запросов к Graph API: отправка, медиа, шаблоны.
GRAPH_TIMEOUT = 15.0

_instance: tuple[str, WhatsAppCloudClient] | None = None


def build_whatsapp_client() -> WhatsAppCloudClient:
    """Боевой клиент при заполненном токене, иначе лог-фолбэк — один на процесс.

    Включение реальной отправки — заполнение ``WA_CLOUD_ACCESS_TOKEN`` без
    правок кода (та же дисциплина, что у FCM/APNs в ``shared/push``). Смена
    токена в настройках (тесты, ротация) пересобирает клиент.
    """
    global _instance
    token = settings.wa_cloud_access_token
    if _instance is None or _instance[0] != token:
        client: WhatsAppCloudClient = (
            GraphWhatsAppClient(
                settings.wa_cloud_api_url,
                token,
                client=shared_async_client("whatsapp", timeout=GRAPH_TIMEOUT),
            )
            if token
            else LoggingWhatsAppClient()
        )
        _instance = (token, client)
    return _instance[1]


def reset_whatsapp_client() -> None:
    """Забыть собранный клиент (тесты)."""
    global _instance
    _instance = None


def build_onboarding_client() -> MetaOnboardingClient | None:
    """Клиент шагов Embedded Signup; ``None`` — приложение Meta не настроено.

    Нужны ID и секрет приложения: секрет подписывает обмен одноразового кода
    на токен клиники. Тот же пул соединений, что у транспорта.
    """
    if not (settings.wa_cloud_app_id and settings.wa_cloud_app_secret):
        return None
    return GraphOnboardingClient(
        settings.wa_cloud_api_url,
        settings.wa_cloud_app_id,
        settings.wa_cloud_app_secret,
        shared_async_client("whatsapp", timeout=GRAPH_TIMEOUT),
    )
