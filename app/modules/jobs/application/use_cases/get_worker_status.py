"""Use-case: состояние воркера — жив ли и что делал каждой джобой.

Отвечает на вопрос «работает ли фон» двумя независимыми источниками:

* **отметка жизни** в Redis, которую воркер обновляет каждую минуту. Она
  показывает сам процесс: живой воркер без работы всё равно отмечается;
* **последний запуск каждой джобы** из журнала. Показывает работу: процесс мог
  быть жив, а джоба падать или не запускаться.

Порознь ни то ни другое не годится. Только по журналу нельзя отличить «воркер
стоит» от «джобе ещё не время» (уборка ходит раз в сутки), а только по отметке
жизни не видно, что каждая её попытка заканчивается ошибкой.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from redis.asyncio import Redis

from app.core.jobs import WORKER_HEARTBEAT_KEY
from app.modules.jobs.infrastructure.repositories.job_run_repository import JobRunRepository
from app.shared.db.models.job_run import JobRun


@dataclass(frozen=True, slots=True)
class WorkerStatus:
    alive: bool
    last_heartbeat_at: datetime | None
    last_runs: list[JobRun]


class GetWorkerStatusUseCase:
    def __init__(self, jobs: JobRunRepository, redis: Redis) -> None:
        self._jobs = jobs
        self._redis = redis

    async def execute(self) -> WorkerStatus:
        raw: str | None = await self._redis.get(WORKER_HEARTBEAT_KEY)
        heartbeat = _parse_heartbeat(raw)
        return WorkerStatus(
            # Ключ истекает сам: его наличие и означает «отмечался недавно».
            alive=raw is not None,
            last_heartbeat_at=heartbeat,
            last_runs=await self._jobs.latest_per_job(),
        )


def _parse_heartbeat(raw: str | None) -> datetime | None:
    """Разобрать отметку; мусор в ключе не должен ронять экран состояния."""
    if raw is None:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
