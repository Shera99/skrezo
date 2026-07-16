"""Таблица пациентов — ГЛОБАЛЬНАЯ сущность платформы.

Пациент НЕ принадлежит клинике: телефон уникален на всю платформу, история
лечения общая. Клиника «видит» пациента через наличие приёмов (`appointment`).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class Patient(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "patient"

    phone: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # E.164, дедуп платформы
    first_name: Mapped[str] = mapped_column(Text, nullable=False)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    middle_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    gender: Mapped[str | None] = mapped_column(Text, nullable=True)  # male|female|other
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar: Mapped[str | None] = mapped_column(Text, nullable=True)
