"""Роут: GET /admin/jobs/runs — журнал запусков фоновых задач."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.modules.jobs.application.use_cases.list_job_runs import ListJobRunsUseCase
from app.modules.jobs.presentation.deps import get_list_job_runs_use_case
from app.modules.jobs.presentation.schemas import JobRunListResponse, JobRunOut
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()


@router.get(
    "/admin/jobs/runs",
    response_model=JobRunListResponse,
    summary="Журнал запусков фоновых задач",
    response_description="Запуски (свежие сверху) и общее число под фильтром",
)
async def list_job_runs(
    job_name: str | None = Query(default=None, alias="job", description="Фильтр по имени задачи"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListJobRunsUseCase = Depends(get_list_job_runs_use_case),
) -> JobRunListResponse:
    """Показать запуски задач: когда, сколько заняли, чем закончились.

    Пагинация смещением, а не курсором: журнал читают глазами с первой
    страницы, и глубоко в него не уходят — записи старше
    ``JOB_RUN_RETENTION_DAYS`` удаляет уборочная задача.
    """
    page = await use_case.execute(job_name=job_name, limit=limit, offset=offset)
    return JobRunListResponse(
        items=[JobRunOut.from_run(run) for run in page.items], total=page.total
    )
