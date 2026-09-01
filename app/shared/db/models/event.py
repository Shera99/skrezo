"""Таблица доменных событий — транзакционный outbox (ТЗ-СУ §1, §2.1).

Строка пишется **в той же транзакции**, что и доменное изменение (см.
``app/modules/push/application/outbox.py``): событие о том, чего не произошло,
разослать невозможно — откат транзакции забирает и его. ``pg_notify`` при этом
лишь будильник для диспетчера; истина — эти строки, и потерянный сигнал
догоняется 30-секундным поллингом.

``processed_at`` ставит диспетчер; ``attempts``/``last_error`` — счётчик
неудачных попыток: после пятой строка больше не берётся и остаётся в журнале
как алерт.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


class Event(Base):
    __tablename__ = "event"
    __table_args__ = (
        # Очередь диспетчера: только необработанные, в порядке появления.
        Index(
            "ix_event_pending",
            "id",
            postgresql_where=text("processed_at IS NULL"),
        ),
        Index("ix_event_clinic_occurred", "clinic_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    # Тип из каталога (app/modules/push/domain/events.py): appointment.created…
    type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Снимок для отладки и параметров текста; текущее состояние сущности
    # диспетчер читает из БД в момент доставки, а не отсюда.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
