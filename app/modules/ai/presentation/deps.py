"""Зависимости модуля AI: провайдер (подменяется в тестах) и сервисы."""

from __future__ import annotations

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.jobs import JobQueue, get_job_queue
from app.core.redis import get_redis
from app.modules.ai.application.drafts import DraftService
from app.modules.ai.application.kb import KbService
from app.modules.ai.application.knowledge_gaps import KnowledgeGapService
from app.modules.ai.application.metrics import AssistantMetricsService
from app.modules.ai.application.regression_cases import RegressionCaseService
from app.modules.ai.application.regression_runner import RegressionRunner
from app.modules.ai.application.reply_service import ProcessAiReplyService
from app.modules.ai.application.trust import TrustLadderService
from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.modules.ai.infrastructure.repositories.kb_repository import KbRepository
from app.modules.ai.infrastructure.repositories.knowledge_gap_repository import (
    KnowledgeGapRepository,
)
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.ai import AiProvider
from app.shared.ai.factory import build_ai_provider
from app.shared.whatsapp_cloud import WhatsAppCloudClient, build_whatsapp_client


def get_ai_provider(redis: Redis = Depends(get_redis)) -> AiProvider | None:
    """Провайдер модели по конфигу; None — ассистент не настроен.

    Redis нужен предохранителю (Ф8): симулятор делит его с воркером, иначе
    ручной прогон не видел бы, что контур на паузе.
    """
    return build_ai_provider(redis)


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


def get_draft_service(
    session: AsyncSession = Depends(get_session),
    ai_repository: AiRepository = Depends(get_ai_repository),
    wa_client: WhatsAppCloudClient = Depends(get_wa_client_for_ai),
    queue: JobQueue = Depends(get_job_queue),
) -> DraftService:
    """Черновики режима «Проверяю» и передача диалога между ботом и человеком."""
    return DraftService(session, ai_repository, WhatsAppRepository(session), wa_client, queue)


def get_knowledge_gap_repository(
    session: AsyncSession = Depends(get_session),
) -> KnowledgeGapRepository:
    return KnowledgeGapRepository(session)


def get_knowledge_gap_service(
    session: AsyncSession = Depends(get_session),
    repository: KnowledgeGapRepository = Depends(get_knowledge_gap_repository),
) -> KnowledgeGapService:
    """Очередь «Обучение»: ответ своими словами и «не нужно отвечать»."""
    return KnowledgeGapService(session, repository)


def get_kb_service(session: AsyncSession = Depends(get_session)) -> KbService:
    """Редактор базы знаний: документы, версии, откат, сводка токенов."""
    return KbService(session, KbRepository(session))


def get_regression_case_service(
    session: AsyncSession = Depends(get_session),
) -> RegressionCaseService:
    return RegressionCaseService(session)


def get_regression_runner(
    session: AsyncSession = Depends(get_session),
    provider: AiProvider | None = Depends(get_ai_provider),
    wa_client: WhatsAppCloudClient = Depends(get_wa_client_for_ai),
) -> RegressionRunner:
    """Прогон регрессии: ставит задание воркеру, без очереди выполняет на месте."""
    return RegressionRunner(session, provider, wa_client)


def get_assistant_metrics_service(
    session: AsyncSession = Depends(get_session),
) -> AssistantMetricsService:
    """Метрики обучения и гейт доверия."""
    return AssistantMetricsService(session)


def get_trust_ladder_service(session: AsyncSession = Depends(get_session)) -> TrustLadderService:
    """Лестница доверия: ступени, гейт, выключатель."""
    return TrustLadderService(session)
