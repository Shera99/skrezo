"""Зависимости ручки голосового помощника: доступ, клиент модели, предохранитель."""

from __future__ import annotations

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import ForbiddenError
from app.core.redis import get_redis
from app.modules.access.application.services.subscription_policy import (
    DENY_MESSAGES,
    DenyReason,
    SubscriptionPolicy,
)
from app.modules.access.presentation.deps import get_clinic_context, get_subscription_policy
from app.modules.llm.application.services.quota import LlmQuota
from app.modules.llm.application.use_cases.generate import GenerateUseCase
from app.modules.llm.infrastructure.gemini_client import GeminiGenerateClient, build_llm_client
from app.modules.subscriptions.domain.access import ClinicAccess
from app.shared.ai.resilience import CircuitBreaker
from app.shared.auth import ClientContext

#: Свои ключи предохранителя: сбой голосового помощника не глушит ассистента
#: пациентов в WhatsApp, и наоборот.
_BREAKER_FAILURES_KEY = "llm:app:failures"
_BREAKER_OPEN_KEY = "llm:app:open"


async def require_llm_access(
    context: ClientContext = Depends(get_clinic_context),
    policy: SubscriptionPolicy = Depends(get_subscription_policy),
) -> ClientContext:
    """Любой сотрудник клиники с действующим членством — и подписка не в режиме чтения.

    Решение владельца 12.10.2026: отдельного права нет, а в режиме чтения
    (кончился триал или оплата) помощник недоступен — каждый вопрос стоит
    денег. Код ответа тот же, что у остальных ручек: ``subscription_inactive``.
    """
    if await policy.access(context.clinic_id) is ClinicAccess.READ_ONLY:
        reason = DenyReason.SUBSCRIPTION_INACTIVE
        raise ForbiddenError(DENY_MESSAGES[reason], code=reason.value)
    return context


def get_llm_client() -> GeminiGenerateClient | None:
    return build_llm_client()


def get_llm_breaker(redis: Redis = Depends(get_redis)) -> CircuitBreaker:
    return CircuitBreaker(
        redis,
        failures_key=_BREAKER_FAILURES_KEY,
        open_key=_BREAKER_OPEN_KEY,
        threshold=settings.ai_breaker_threshold,
        open_seconds=settings.ai_breaker_open_seconds,
        name="llm_app",
    )


def get_generate_use_case(
    session: AsyncSession = Depends(get_session),
    client: GeminiGenerateClient | None = Depends(get_llm_client),
    breaker: CircuitBreaker = Depends(get_llm_breaker),
    redis: Redis = Depends(get_redis),
) -> GenerateUseCase:
    return GenerateUseCase(session, client, breaker, LlmQuota(redis))
