"""Таблица мягких резервов слота — предложения записи от ассистента (§6.4 ТЗ).

``propose_appointment`` держит слот 10 минут, чтобы двое пациентов не заняли
одно кресло за время диалога, и возвращает ``confirmation_id`` (= id строки).
Запись в ``appointment`` создаёт ТОЛЬКО обработчик кнопки подтверждения —
модель к этому шагу технически не допускается (И3).

Протухший резерв безвреден: ``expires_at`` проверяется при подтверждении
(«предложение больше не действует» + свежие слоты), строки убирает ночная
чистка. ``consumed_at``/``appointment_id`` делают подтверждение идемпотентным:
двойное нажатие кнопки не создаст второй приём.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class SlotHold(Base, UUIDPKMixin):
    __tablename__ = "slot_hold"
    __table_args__ = (
        # Проверка «слот уже кем-то предложен» и уборка протухших.
        Index("ix_slot_hold_doctor_time", "doctor_membership_id", "starts_at"),
        Index("ix_slot_hold_expires", "expires_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_conversation.id", ondelete="CASCADE"), nullable=False
    )
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False
    )
    doctor_membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("membership.id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("service.id", ondelete="SET NULL"), nullable=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False)
    # Человекочитаемая карточка — то, что видел пациент при подтверждении.
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # Пожелание пациента его словами (жалоба, зуб, вторая процедура) — уйдёт
    # в комментарий приёма, чтобы врач увидел его до визита.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appointment.id", ondelete="SET NULL"), nullable=True
    )
    # Ф7: вид предложения — book (новая запись) | reschedule | cancel — и
    # запись, которую меняем; после подтверждения appointment_id = она же.
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="book")
    target_appointment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("appointment.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
