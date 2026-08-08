"""Arq-воркер фоновых задач.

Пока задача одна — закрытие истёкших триалов. Она **не** отвечает за доступ:
его закрывает политика подписки по дате окончания триала, иначе упавший воркер
раздавал бы бесплатный доступ. Джоба приводит статус в БД в соответствие с
реальностью, чтобы админка, дашборд и история подписки не врали.

Воркер — тонкий адаптер: расписание и запуск здесь, работа — в use-case модуля
``subscriptions``. Джобы биллинга (списания, продления) появятся рядом на фазе 5.
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.db import session_factory
from app.modules.subscriptions.application.use_cases.expire_trials import ExpireTrialsUseCase
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)

logger = structlog.get_logger(__name__)


async def expire_trials(ctx: dict[str, Any]) -> int:
    """Перевести истёкшие триалы в ``trial_expired`` (ежечасно)."""
    async with session_factory() as session:
        use_case = ExpireTrialsUseCase(session, SubscriptionRepository(session))
        count = await use_case.execute()
    if count:
        logger.info("trials_expired", count=count)
    return count


class WorkerSettings:
    """Настройки arq-воркера."""

    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions: ClassVar[list[Any]] = [expire_trials]
    # Раз в час, а не раз в сутки: триал кончается в конкретный момент, и сутки
    # расхождения статуса заметны и клиенту, и в дашборде. Минута 5 — чтобы не
    # совпадать с ровным часом, когда просыпается всё остальное.
    cron_jobs: ClassVar[list[Any]] = [cron(expire_trials, minute=5)]
