"""Таблица записей по зубу (манипуляция/диагноз в рамках визита).

Каждая строка — событие по конкретному зубу (нотация FDI) в рамках визита.
«История зуба» = все записи пациента по данному номеру зуба по времени.
Одонтограмма (текущее состояние) — последняя запись по каждому зубу.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class ToothRecord(Base, UUIDPKMixin):
    __tablename__ = "tooth_record"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False, index=True
    )
    visit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("visit.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tooth: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # номер FDI (11..48, 51..85)
    diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    treatment: Mapped[str | None] = mapped_column(Text, nullable=True)  # что сделано
    status: Mapped[str | None] = mapped_column(Text, nullable=True)  # состояние зуба после
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
