"""Отметка «напомнили»: сотрудник связался с пациентом по поводу.

Раньше отметки жили в браузере, и второй администратор их не видел. Это
журнал факта звонка или сообщения, а не рассылка: автоматические напоминания
ассистента живут в ``patient_reminder`` и ``appointment_reminder``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class ReminderMark(Base, UUIDPKMixin):
    __tablename__ = "reminder_mark"
    __table_args__ = (
        # Экран «Напоминания»: последняя отметка по каждому пациенту клиники.
        Index("ix_reminder_mark_clinic_patient", "clinic_id", "patient_id", "marked_at"),
        # Журнал отметок клиники за период (последние сверху).
        Index("ix_reminder_mark_clinic_marked", "clinic_id", "marked_at"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )
    #: Повод: recall | debt | visit | other (домен ``reminders``).
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    #: Приём-источник повода (долг по нему, визит, последний осмотр) — необязателен.
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appointment.id", ondelete="SET NULL"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Кто отметил — членство в клинике; при удалении сотрудника отметка остаётся.
    marked_by_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("membership.id", ondelete="SET NULL"), nullable=True
    )
    marked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
