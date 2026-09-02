"""Зависимости модуля AI: провайдер (подменяется в тестах) и сервисы."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.ai.application.reply_service import ProcessAiReplyService
from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.shared.ai import AiProvider
from app.shared.ai.factory import build_ai_provider
from app.shared.whatsapp_cloud import WhatsAppCloudClient, build_whatsapp_client


def get_ai_provider() -> AiProvider | None:
    """Провайдер модели по конфигу; None — ассистент не настроен."""
    return build_ai_provider()


def get_wa_client_for_ai() -> WhatsAppCloudClient:
    return build_whatsapp_client()


def get_ai_repository(session: AsyncSession = Depends(get_session)) -> AiRepository:
    return AiRepository(session)


def get_reply_service(
    session: AsyncSession = Depends(get_session),
    provider: AiProvider | None = Depends(get_ai_provider),
    wa_client: WhatsAppCloudClient = Depends(get_wa_client_for_ai),
) -> ProcessAiReplyService:
    return ProcessAiReplyService(session, provider, wa_client)
