"""Зависимости модуля фоновых задач."""

from __future__ import annotations

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.redis import get_redis
from app.modules.jobs.application.use_cases.get_worker_status import GetWorkerStatusUseCase
from app.modules.jobs.application.use_cases.list_job_runs import ListJobRunsUseCase
from app.modules.jobs.infrastructure.repositories.job_run_repository import JobRunRepository


def get_job_run_repository(session: AsyncSession = Depends(get_session)) -> JobRunRepository:
    return JobRunRepository(session)


def get_worker_status_use_case(
    jobs: JobRunRepository = Depends(get_job_run_repository),
    redis: Redis = Depends(get_redis),
) -> GetWorkerStatusUseCase:
    return GetWorkerStatusUseCase(jobs, redis)


def get_list_job_runs_use_case(
    jobs: JobRunRepository = Depends(get_job_run_repository),
) -> ListJobRunsUseCase:
    return ListJobRunsUseCase(jobs)
