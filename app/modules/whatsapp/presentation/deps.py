"""Зависимости модуля WhatsApp: репозиторий, клиент Cloud API, use-case'ы."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.whatsapp.application.use_cases.connect_whatsapp import ConnectWhatsAppUseCase
from app.modules.whatsapp.application.use_cases.connection_status import (
    GetConnectionStatusUseCase,
)
from app.modules.whatsapp.application.use_cases.disconnect_whatsapp import (
    DisconnectWhatsAppUseCase,
)
from app.modules.whatsapp.application.use_cases.embedded_signup import (
    ConnectViaEmbeddedSignupUseCase,
)
from app.modules.whatsapp.application.use_cases.get_messages import GetMessagesUseCase
from app.modules.whatsapp.application.use_cases.list_conversations import (
    ListConversationsUseCase,
)
from app.modules.whatsapp.application.use_cases.list_history import ListHistoryUseCase
from app.modules.whatsapp.application.use_cases.mark_read import MarkConversationReadUseCase
from app.modules.whatsapp.application.use_cases.prefill_template import PrefillTemplateUseCase
from app.modules.whatsapp.application.use_cases.refresh_connection import (
    RefreshConnectionUseCase,
)
from app.modules.whatsapp.application.use_cases.send_message import SendMessageUseCase
from app.modules.whatsapp.application.use_cases.send_template import SendTemplateUseCase
from app.modules.whatsapp.application.use_cases.set_template_labels import (
    SetTemplateLabelsUseCase,
)
from app.modules.whatsapp.application.use_cases.sync_templates import SyncTemplatesUseCase
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.whatsapp_cloud import (
    MetaOnboardingClient,
    WhatsAppCloudClient,
    build_onboarding_client,
    build_whatsapp_client,
)


def get_whatsapp_repository(
    session: AsyncSession = Depends(get_session),
) -> WhatsAppRepository:
    return WhatsAppRepository(session)


def get_whatsapp_client() -> WhatsAppCloudClient:
    """Клиент Cloud API (боевой либо лог-фолбэк — по конфигу)."""
    return build_whatsapp_client()


def get_list_conversations_use_case(
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> ListConversationsUseCase:
    return ListConversationsUseCase(repository)


def get_mark_read_use_case(
    session: AsyncSession = Depends(get_session),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> MarkConversationReadUseCase:
    return MarkConversationReadUseCase(session, repository)


def get_get_messages_use_case(
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> GetMessagesUseCase:
    return GetMessagesUseCase(repository)


def get_send_message_use_case(
    session: AsyncSession = Depends(get_session),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
    client: WhatsAppCloudClient = Depends(get_whatsapp_client),
) -> SendMessageUseCase:
    return SendMessageUseCase(session, repository, client)


def get_connect_whatsapp_use_case(
    session: AsyncSession = Depends(get_session),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> ConnectWhatsAppUseCase:
    return ConnectWhatsAppUseCase(session, repository)


def get_sync_templates_use_case(
    session: AsyncSession = Depends(get_session),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
    client: WhatsAppCloudClient = Depends(get_whatsapp_client),
) -> SyncTemplatesUseCase:
    return SyncTemplatesUseCase(session, repository, client)


def get_send_template_use_case(
    session: AsyncSession = Depends(get_session),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
    client: WhatsAppCloudClient = Depends(get_whatsapp_client),
) -> SendTemplateUseCase:
    return SendTemplateUseCase(session, repository, client)


def get_set_template_labels_use_case(
    session: AsyncSession = Depends(get_session),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> SetTemplateLabelsUseCase:
    return SetTemplateLabelsUseCase(session, repository)


def get_prefill_template_use_case(
    session: AsyncSession = Depends(get_session),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> PrefillTemplateUseCase:
    return PrefillTemplateUseCase(session, repository)


def get_onboarding_client() -> MetaOnboardingClient | None:
    """Шаги Embedded Signup в Graph API; ``None`` — приложение Meta не настроено."""
    return build_onboarding_client()


def get_connection_status_use_case(
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> GetConnectionStatusUseCase:
    return GetConnectionStatusUseCase(repository)


def get_embedded_signup_use_case(
    session: AsyncSession = Depends(get_session),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
    onboarding: MetaOnboardingClient | None = Depends(get_onboarding_client),
    client: WhatsAppCloudClient = Depends(get_whatsapp_client),
) -> ConnectViaEmbeddedSignupUseCase:
    return ConnectViaEmbeddedSignupUseCase(session, repository, onboarding, client)


def get_refresh_connection_use_case(
    session: AsyncSession = Depends(get_session),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
    onboarding: MetaOnboardingClient | None = Depends(get_onboarding_client),
) -> RefreshConnectionUseCase:
    return RefreshConnectionUseCase(session, repository, onboarding)


def get_disconnect_whatsapp_use_case(
    session: AsyncSession = Depends(get_session),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
    onboarding: MetaOnboardingClient | None = Depends(get_onboarding_client),
) -> DisconnectWhatsAppUseCase:
    return DisconnectWhatsAppUseCase(session, repository, onboarding)


def get_list_history_use_case(
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> ListHistoryUseCase:
    return ListHistoryUseCase(repository)
