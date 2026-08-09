"""Таблица запусков фоновых задач (журнал воркера).

Нужна ровно для одного вопроса: работает ли воркер и что у него происходит.
Логов для этого мало — их читают, когда уже знают, что искать, а по строкам
таблицы видно и обратное: джоба не запускалась вовсе.

Строка пишется **дважды**: в начале со статусом ``running`` и в конце с исходом.
Иначе зависшая или убитая на полпути задача не оставляла бы следа — а именно её
и нужно заметить.

Строки чистит сама же джоба ``cleanup_job_runs`` (см. ``JOB_RUN_RETENTION_DAYS``):
таблица растёт с каждым уведомлением, и бесконечно хранить её незачем.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin
from app.shared.db.enums import JobRunStatus, JobRunStatusType


class JobRun(Base, UUIDPKMixin):
    __tablename__ = "job_run"

    job_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[JobRunStatus] = mapped_column(JobRunStatusType, nullable=False)
    #: Номер попытки arq (1 — первая). Больше единицы = задача уже падала.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Результат («переведено 3») или текст ошибки — читается человеком, не кодом.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Экран состояния всегда спрашивает «последние запуски», обычно по одной
        # джобе: без индекса это seq scan по таблице, которая растёт от каждого
        # отправленного сообщения.
        Index("ix_job_run_name_started", "job_name", started_at.desc()),
    )
