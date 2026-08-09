"""Репозиторий журнала запусков фоновых задач."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import JobRunStatus
from app.shared.db.models.job_run import JobRun


class JobRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(self, job_name: str, attempt: int) -> uuid.UUID:
        """Открыть запись запуска (статус ``running``); вернуть её id."""
        run = JobRun(job_name=job_name, status=JobRunStatus.RUNNING, attempt=attempt)
        self._session.add(run)
        await self._session.flush()
        return run.id

    async def finish(
        self, run_id: uuid.UUID, *, status: JobRunStatus, detail: str | None, finished_at: datetime
    ) -> None:
        """Закрыть запись запуска исходом."""
        run = await self._session.get(JobRun, run_id)
        if run is None:  # pragma: no cover — запись только что создана этим же кодом
            return
        run.status = status
        run.detail = detail
        run.finished_at = finished_at

    async def latest_per_job(self) -> list[JobRun]:
        """По одной последней записи на каждую джобу — для экрана состояния."""
        rows = await self._session.execute(
            select(JobRun)
            .distinct(JobRun.job_name)
            .order_by(JobRun.job_name, desc(JobRun.started_at))
        )
        return list(rows.scalars())

    async def list_runs(
        self, *, job_name: str | None, limit: int, offset: int
    ) -> tuple[list[JobRun], int]:
        """Журнал запусков (новые сверху) и общее число строк под фильтром."""
        condition = JobRun.job_name == job_name if job_name else None

        query = select(JobRun).order_by(desc(JobRun.started_at)).limit(limit).offset(offset)
        total_query = select(func.count()).select_from(JobRun)
        if condition is not None:
            query = query.where(condition)
            total_query = total_query.where(condition)

        rows = (await self._session.execute(query)).scalars()
        total = (await self._session.execute(total_query)).scalar_one()
        return list(rows), total

    async def delete_older_than(self, moment: datetime) -> int:
        """Удалить записи старше момента; вернуть их число."""
        rows = (
            await self._session.execute(
                delete(JobRun).where(JobRun.started_at < moment).returning(JobRun.id)
            )
        ).scalars()
        return len(list(rows))
