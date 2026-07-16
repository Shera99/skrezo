"""Таблица зубов пациента — ТЕКУЩЕЕ состояние (одонтограмма).

Один зуб пациента существует ровно один раз (UNIQUE patient_id + tooth_number) и
обновляется по ходу лечения. История манипуляций — в ``appointment_tooth``.
Пациент глобальный → одонтограмма общая на всю платформу.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, SmallInteger, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import ToothStatus, ToothStatusType


class PatientTooth(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "patient_tooth"
    __table_args__ = (
        UniqueConstraint("patient_id", "tooth_number", name="uq_patient_tooth"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tooth_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # FDI: 11–48, 51–85
    status: Mapped[ToothStatus] = mapped_column(
        ToothStatusType, nullable=False, default=ToothStatus.HEALTHY
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
