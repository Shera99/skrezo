"""Напоминание пациенту по расписанию: долг или повторный осмотр (Ф7).

Одно на источник (``kind`` + приём-источник). Статусы: ``sent`` — ушло;
``answered`` — нажал «Записаться» (повторный осмотр); ``snoozed`` — нажал
«Не сейчас»; ``failed`` — не отправилось (причина в ``note``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class PatientReminder(Base, UUIDPKMixin):
    __tablename__ = "patient_reminder"
    __table_args__ = (
        UniqueConstraint("kind", "appointment_id", name="uq_patient_reminder_source"),
        Index("ix_patient_reminder_clinic_kind", "clinic_id", "kind", "status"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )
    #: debt | recall
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    #: Приём-источник: долг по нему либо последний завершённый визит.
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("appointment.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_conversation.id", ondelete="SET NULL"), nullable=True
    )
    wa_message_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="sent")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
