"""Таблица идемпотентности пушей — журнал уже отправленных событий.

Нужна только там, где событие ищет ПЛАНИРОВЩИК (`appointment.soon`,
`appointment.arrival`, сводки дня): скан идёт по расписанию и должен уметь
спросить «этому приёму этот пуш уже слали?». Для событий, порождённых из
запроса, идемпотентность даёт ``job_id`` очереди arq — эта таблица не нужна.

MVP-упрощение (см. ``docs/PUSH_NOTIFICATIONS.md``, §3.2): в инкременте P0-a
таблица только заводится — пишет и читает её ``PushDispatcher`` и cron-сканы,
которых пока нет.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


class PushEvent(Base):
    __tablename__ = "push_event"
    __table_args__ = (Index("ix_push_event_clinic", "clinic_id"),)

    # Не суррогат: это будущий `data.id` пуша (например `evt_...`) — стабильный
    # идентификатор, по которому приложение отзывает дубли.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    # = collapseKey; по нему скан ищет «этому приёму такой пуш уже слали?».
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
