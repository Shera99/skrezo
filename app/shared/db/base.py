"""Базовый класс ORM и общие миксины.

Все модели всех модулей наследуют один :class:`Base`, чтобы делить общую
``MetaData`` (нужно для миграций Alembic и разрешения межмодульных FK).
Сами модели лежат в ``app/shared/db/models`` — по файлу на таблицу; модули
переиспользуют их.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db.enums import Lang, LangType


class Base(DeclarativeBase):
    """Общий декларативный базовый класс."""


class UUIDPKMixin:
    """Первичный ключ UUID v4."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """Метки времени создания/обновления (timestamptz, серверные)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TranslationMixin:
    """Переиспользуемая часть таблиц переводов: язык + значение.

    Конкретная таблица добавляет FK на родителя и ``UniqueConstraint`` по
    ``(<parent>_id, lang)``.
    """

    lang: Mapped[Lang] = mapped_column(LangType, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
