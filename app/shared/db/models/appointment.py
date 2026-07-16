"""Таблица приёмов — связывает клинику, врача и пациента.

Врач = наш ``membership`` (account+роль), а не отдельная сущность. Итоговая
стоимость приёма — ``price_total``; манипуляции по зубам — в ``appointment_tooth``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import (
    AppointmentStatus,
    AppointmentStatusType,
    AppointmentType,
    AppointmentTypeType,
)


class Appointment(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "appointment"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Врач — членство в клинике (наш account+роль); необязательно.
    doctor_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("membership.id", ondelete="SET NULL"), nullable=True
    )
    appointment_type: Mapped[AppointmentType] = mapped_column(
        AppointmentTypeType, nullable=False, default=AppointmentType.TREATMENT
    )
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    status: Mapped[AppointmentStatus] = mapped_column(
        AppointmentStatusType, nullable=False, default=AppointmentStatus.SCHEDULED
    )
    price_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # целые сомы
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
