"""Напоминание о визите, ушедшее пациенту в WhatsApp (Ф7).

Одно на приём. Статусы: ``sent`` — ушло, ждём ответа; ``confirmed`` — нажал
«Приду»; ``change_requested`` — нажал «Перенести»; ``no_answer`` — не
ответил к контрольному сроку, администратору ушло событие; ``failed`` —
отправить не удалось (причина в ``note``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class AppointmentReminder(Base, UUIDPKMixin):
    __tablename__ = "appointment_reminder"
    __table_args__ = (
        UniqueConstraint("appointment_id", name="uq_appointment_reminder_appointment"),
        Index("ix_appointment_reminder_clinic_status", "clinic_id", "status"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
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
