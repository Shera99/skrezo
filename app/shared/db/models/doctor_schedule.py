"""Индивидуальный график врача по дням недели.

Сужает часы клиники: реальное окно приёма = пересечение часов клиники и этого
графика. Строки может не быть — тогда врач работает по часам клиники.
Дни недели — как в :meth:`datetime.date.weekday`: 0 = понедельник … 6 = воскресенье.
"""

from __future__ import annotations

import uuid
from datetime import time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    SmallInteger,
    Time,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class DoctorSchedule(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "doctor_schedule"
    __table_args__ = (
        UniqueConstraint(
            "clinic_id", "membership_id", "weekday", name="uq_doctor_schedule_weekday"
        ),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_doctor_schedule_weekday"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    # Врач = членство в клинике (account+роль со специализацией).
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("membership.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    starts_at: Mapped[time] = mapped_column(Time, nullable=False)
    ends_at: Mapped[time] = mapped_column(Time, nullable=False)
    is_working: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
