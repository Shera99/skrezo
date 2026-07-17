"""Часы работы клиники по дням недели (общая сетка «ленты дня»).

Ровно одна строка на клинику и день недели: во сколько клиника открывается и
закрывается и когда общий обед (полоса через все колонки врачей). Дни недели —
как в :meth:`datetime.date.weekday`: 0 = понедельник … 6 = воскресенье.
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


class ClinicWorkingHours(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "clinic_working_hours"
    __table_args__ = (
        UniqueConstraint("clinic_id", "weekday", name="uq_clinic_working_hours_weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_clinic_working_hours_weekday"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 0 = понедельник … 6 = воскресенье (date.weekday()).
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    opens_at: Mapped[time] = mapped_column(Time, nullable=False)
    closes_at: Mapped[time] = mapped_column(Time, nullable=False)
    # Обед — общая полоса для всех врачей; NULL = обеда в этот день нет.
    break_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    break_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    is_working: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
