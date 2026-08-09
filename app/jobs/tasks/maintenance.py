"""Задачи обслуживания: уборка журнала и отметка жизни воркера."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.db import session_factory
from app.core.jobs import WORKER_HEARTBEAT_KEY, WORKER_HEARTBEAT_TTL_SECONDS
from app.jobs.tasks.context import attempt
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.modules.jobs.infrastructure.repositories.job_run_repository import JobRunRepository


async def cleanup_job_runs(ctx: dict[str, Any]) -> int:
    """Удалить записи журнала старше ``JOB_RUN_RETENTION_DAYS``."""
    async with JobRunRecorder("cleanup_job_runs", attempt(ctx)) as run:
        cutoff = datetime.now(UTC) - timedelta(days=settings.job_run_retention_days)
        async with session_factory() as session:
            deleted = await JobRunRepository(session).delete_older_than(cutoff)
            await session.commit()
        run.note(f"удалено записей: {deleted}")
        return deleted


async def heartbeat(ctx: dict[str, Any]) -> None:
    """Отметить, что воркер жив.

    В журнал не пишется: это признак жизни, а не работа, и 1440 строк в сутки
    сделали бы журнал нечитаемым. Отметка живёт в Redis и истекает сама — по её
    отсутствию и видно, что воркер встал.
    """
    redis = ctx.get("redis")
    if redis is None:  # pragma: no cover — вне воркера контекста нет
        return
    await redis.set(
        WORKER_HEARTBEAT_KEY,
        datetime.now(UTC).isoformat(),
        ex=WORKER_HEARTBEAT_TTL_SECONDS,
    )
