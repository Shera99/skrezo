"""Запись запуска задачи в журнал — обёртка вокруг тела джобы.

Три решения, которые здесь важнее кода:

* **Своя сессия.** Журнал пишется отдельной сессией, а не той, в которой
  работает задача: у упавшей задачи транзакция откатывается, и запись о её
  падении откатилась бы вместе с ней — то есть исчезла бы ровно та строка,
  ради которой всё и затевалось.
* **Две записи.** Строка открывается до работы (``running``) и закрывается
  после. Иначе убитая на полпути задача не оставляет следа и выглядит как
  «не запускалась».
* **Журнал не мешает работе.** Своя ошибка (БД недоступна) уходит в лог и
  гасится: не записать запуск неприятно, а уронить из-за этого джобу — хуже.
  Исключение самой задачи, наоборот, пропускается наружу: по нему arq решает,
  повторять ли попытку.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import TracebackType
from typing import Self

import structlog

from app.core.db import session_factory
from app.modules.jobs.infrastructure.repositories.job_run_repository import JobRunRepository
from app.shared.db.enums import JobRunStatus

logger = structlog.get_logger(__name__)


class JobRunRecorder:
    """Контекст выполнения джобы: открывает запись в журнале и закрывает исходом."""

    def __init__(self, job_name: str, attempt: int = 1) -> None:
        self._job_name = job_name
        self._attempt = attempt
        self._run_id: uuid.UUID | None = None
        self._detail: str | None = None

    def note(self, detail: str) -> None:
        """Что сделала задача — человекочитаемо («переведено: 3»)."""
        self._detail = detail

    async def __aenter__(self) -> Self:
        try:
            async with session_factory() as session:
                self._run_id = await JobRunRepository(session).start(self._job_name, self._attempt)
                await session.commit()
        except Exception as exc:  # журнал не должен ронять задачу
            logger.error("job_run_start_not_recorded", job=self._job_name, error=str(exc))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        status = JobRunStatus.OK if exc is None else JobRunStatus.FAILED
        # На провале сохраняем и заметку задачи: у arq-``Retry`` в тексте только
        # задержка, а настоящая причина («провайдер отклонил») — как раз в ней.
        detail = self._detail
        if exc is not None:
            reason = f"{type(exc).__name__}: {exc}"
            detail = f"{detail}; {reason}" if detail else reason
        if self._run_id is not None:
            try:
                async with session_factory() as session:
                    await JobRunRepository(session).finish(
                        self._run_id,
                        status=status,
                        detail=detail,
                        finished_at=datetime.now(UTC),
                    )
                    await session.commit()
            except Exception as write_error:  # см. выше — журнал вторичен
                logger.error(
                    "job_run_finish_not_recorded", job=self._job_name, error=str(write_error)
                )
        if exc is not None:
            logger.error("job_failed", job=self._job_name, error=str(exc))
        # Ничего не возвращаем: исключение задачи уходит наружу, по нему arq и
        # решает, повторять ли попытку. Вернуть True значило бы его проглотить.
