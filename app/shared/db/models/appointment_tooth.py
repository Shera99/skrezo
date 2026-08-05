"""Таблица истории лечения — одна запись = работа над одним зубом в приёме.

Несколько манипуляций по одному зубу в приёме = несколько строк. История зуба
собирается JOIN'ом ``patient_tooth → appointment_tooth → appointment`` и не
теряется (сквозная по всем клиникам).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin
from app.shared.db.enums import AppointmentToothStatus, AppointmentToothStatusType


class AppointmentTooth(Base, UUIDPKMixin):
    __tablename__ = "appointment_tooth"

    appointment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("appointment.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_tooth_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient_tooth.id", ondelete="CASCADE"), nullable=False, index=True
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("service.id", ondelete="SET NULL"), nullable=True
    )
    doctor_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("membership.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[AppointmentToothStatus] = mapped_column(
        AppointmentToothStatusType, nullable=False, default=AppointmentToothStatus.PLANNED
    )
    price_tiyin: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # тыйыны
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
