"""Таблица кассы клиники — леджер платежей и возвратов по приёмам.

Долг моделируется по приёму: остаток приёма = ``appointment.price_total`` минус
сумма платежей плюс сумма возвратов. Одна строка = одна кассовая операция
(``kind``): приход (``payment``) или возврат (``refund``). Возврат ссылается на
исходный платёж (``refunds_payment_id``).

Это касса КЛИНИКИ (оплата приёмов пациентами), не путать с ``platform_payment`` —
биллингом Skrezo (подписки клиник).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin
from app.shared.db.enums import (
    PaymentKind,
    PaymentKindType,
    PaymentMethod,
    PaymentMethodType,
)


class CashPayment(Base, UUIDPKMixin):
    __tablename__ = "cash_payment"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_cash_payment_amount_positive"),
        # Журнал кассы клиники — по клинике и времени (последние сверху).
        Index("ix_cash_payment_clinic_created", "clinic_id", "created_at"),
        # Свой журнал кассира (RBAC cash.view_own) — по клинике, кассиру и времени.
        Index(
            "ix_cash_payment_recorded_by",
            "clinic_id",
            "recorded_by_membership_id",
            "created_at",
        ),
        # Расчёт остатка по приёму — сумма операций одного приёма.
        Index("ix_cash_payment_appointment", "appointment_id"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    # Приём — биллинговая единица; долг считается по нему.
    appointment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("appointment.id", ondelete="CASCADE"), nullable=False
    )
    # Пациент денормализован для фильтра журнала и долга по пациенту.
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)  # целые сомы, всегда > 0
    method: Mapped[PaymentMethod] = mapped_column(PaymentMethodType, nullable=False)
    kind: Mapped[PaymentKind] = mapped_column(
        PaymentKindType, nullable=False, default=PaymentKind.PAYMENT
    )
    # Кассир = членство в клинике («свой» для cash.view_own). SET NULL при удалении.
    recorded_by_membership_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("membership.id", ondelete="SET NULL"), nullable=True
    )
    # Для возврата — ссылка на исходный платёж (аудит и защита от двойного возврата).
    refunds_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cash_payment.id", ondelete="SET NULL"), nullable=True
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
