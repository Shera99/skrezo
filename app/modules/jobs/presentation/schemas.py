"""Схемы состояния воркера и журнала запусков."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.modules.jobs.application.use_cases.get_worker_status import WorkerStatus
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import JobRunStatus
from app.shared.db.models.job_run import JobRun


class JobRunOut(CamelModel):
    id: uuid.UUID
    job_name: str = Field(description="Имя задачи (expire_trials, send_notification, …)")
    status: JobRunStatus = Field(
        description="running — начата и не завершилась (в т.ч. воркер упал), ok, failed"
    )
    attempt: int = Field(description="Номер попытки arq; больше 1 — задача уже падала")
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = Field(
        default=None, description="Длительность выполнения; null — запуск не завершён"
    )
    detail: str | None = Field(
        default=None, description="Результат («переведено клиник: 3») или текст ошибки"
    )

    @classmethod
    def from_run(cls, run: JobRun) -> JobRunOut:
        duration = (
            int((run.finished_at - run.started_at).total_seconds() * 1000)
            if run.finished_at is not None
            else None
        )
        return cls(
            id=run.id,
            job_name=run.job_name,
            status=run.status,
            attempt=run.attempt,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_ms=duration,
            detail=run.detail,
        )


class WorkerStatusResponse(CamelModel):
    """Состояние фоновых задач: жив ли воркер и чем закончился каждый вид задач."""

    alive: bool = Field(
        description="Воркер отмечался в последние минуты. false — процесс не запущен или встал"
    )
    last_heartbeat_at: datetime | None = Field(
        default=None, description="Когда воркер отмечался последний раз"
    )
    last_runs: list[JobRunOut] = Field(description="По последнему запуску на каждую задачу")

    @classmethod
    def from_status(cls, status: WorkerStatus) -> WorkerStatusResponse:
        return cls(
            alive=status.alive,
            last_heartbeat_at=status.last_heartbeat_at,
            last_runs=[JobRunOut.from_run(run) for run in status.last_runs],
        )


class JobRunListResponse(CamelModel):
    items: list[JobRunOut]
    total: int = Field(description="Всего записей под фильтром (для постраничной навигации)")
