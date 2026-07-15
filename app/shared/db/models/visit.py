"""Таблица визитов (приёмов) пациента.

Визит = одно посещение: врач, дата, жалоба/заметка. Манипуляции по зубам в рамках
визита — строки ``tooth_record`` со ссылкой ``visit_id``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class Visit(Base, UUIDPKMixin):
    __tablename__ = "visit"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Врач, проводивший приём (членство в клинике); необязательно.
    doctor_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("membership.id", ondelete="SET NULL"), nullable=True
    )
    visited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    complaint: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
