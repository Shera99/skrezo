"""Отсутствия врача: отпуск, больничный, разовая недоступность.

Интервал абсолютный (timestamptz), а не «по дню недели»: перекрывает и график
врача, и часы клиники. Пересечение с отсутствием делает слот недоступным.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class DoctorTimeOff(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "doctor_time_off"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_doctor_time_off_range"),
        # Выборка «отсутствия врача за день» — по клинике+врачу с фильтром по времени.
        Index("ix_doctor_time_off_lookup", "clinic_id", "membership_id", "starts_at", "ends_at"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("membership.id", ondelete="CASCADE"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
