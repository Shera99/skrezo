"""Сброс демо-клиник по расписанию — каждый день в 06:00 по времени клиники.

Тик несколько раз в течение часа, а не один раз: упавший прогон повторит
следующий, а уже собранный день второй раз не пересобирается. Каждая клиника —
своей сессией: неготовая (нет врачей или прайса) не мешает остальным.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from app.core.config import settings
from app.core.db import session_factory
from app.jobs.tasks.context import attempt
from app.modules.demo.application.services.demo_reset_service import build_demo_reset_service
from app.modules.demo.domain.day_plan import is_reset_due
from app.modules.demo.infrastructure.repositories.demo_repository import DemoRepository
from app.modules.jobs.application.services.job_recorder import JobRunRecorder

logger = structlog.get_logger(__name__)


async def reset_demo_clinics(ctx: dict[str, Any], now: datetime | None = None) -> str:
    """Собрать день у демо-клиник, где наступило 06:00; вернуть сводку."""
    now = now or datetime.now(UTC)
    async with JobRunRecorder("reset_demo_clinics", attempt(ctx)) as run:
        async with session_factory() as session:
            clinics = await DemoRepository(session).demo_clinics()
        due = [
            clinic
            for clinic in clinics
            if is_reset_due(now, clinic.timezone, clinic.reset_at, settings.demo_reset_hour)
        ]
        done = failed = 0
        for clinic in due:
            try:
                async with session_factory() as session:
                    # Без «задним числом»: пуш «не подтверждён» в шесть утра не нужен.
                    await build_demo_reset_service(session).reset(
                        clinic.clinic_id, now, backdate=False
                    )
                done += 1
            except Exception as exc:
                failed += 1
                logger.error("demo_reset_failed", clinic_id=str(clinic.clinic_id), error=str(exc))
        summary = f"собрано: {done}, не удалось: {failed}"
        run.note(summary)
        return summary
