"""Use-case: журнал запусков фоновых задач (с фильтром по имени)."""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.jobs.infrastructure.repositories.job_run_repository import JobRunRepository
from app.shared.db.models.job_run import JobRun


@dataclass(frozen=True, slots=True)
class JobRunsPage:
    items: list[JobRun]
    total: int


class ListJobRunsUseCase:
    def __init__(self, jobs: JobRunRepository) -> None:
        self._jobs = jobs

    async def execute(self, *, job_name: str | None, limit: int, offset: int) -> JobRunsPage:
        """Страница журнала (новые сверху) и общее число записей под фильтром."""
        items, total = await self._jobs.list_runs(job_name=job_name, limit=limit, offset=offset)
        return JobRunsPage(items=items, total=total)
