"""Таблица пациентов клиники (клиническое ядро — источник правды здесь).

Пациент привязан к клинике (тенант). Одонтограмма и история — производные от
``tooth_record``; сам пациент хранит только карточку.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class Patient(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "patient"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(Text, nullable=True)  # male|female|other (свободно)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
