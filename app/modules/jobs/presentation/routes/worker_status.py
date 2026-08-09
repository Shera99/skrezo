"""Роут: GET /admin/jobs — состояние фоновых задач."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.modules.jobs.application.use_cases.get_worker_status import GetWorkerStatusUseCase
from app.modules.jobs.presentation.deps import get_worker_status_use_case
from app.modules.jobs.presentation.schemas import WorkerStatusResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()


@router.get(
    "/admin/jobs",
    response_model=WorkerStatusResponse,
    summary="Состояние воркера фоновых задач",
    response_description="Признак жизни воркера и последний запуск каждой задачи",
)
async def worker_status(
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetWorkerStatusUseCase = Depends(get_worker_status_use_case),
) -> WorkerStatusResponse:
    """Показать, работает ли фон и чем закончился последний запуск каждой задачи.

    ``alive`` — воркер отмечался в последние минуты; ``false`` значит, что
    процесс не запущен или встал, и тогда триалы не закрываются, а сообщения
    копятся в очереди.

    ``lastRuns`` — по одной последней записи на задачу. Задачи, которая никогда
    не запускалась, в списке не будет вовсе: это отдельный симптом, его не
    видно ни в логах, ни по статусу процесса.
    """
    return WorkerStatusResponse.from_status(await use_case.execute())
