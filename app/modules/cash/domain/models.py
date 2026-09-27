"""Доменные структуры кассы: платёж, страница журнала, сводка, долг по приёму.

Все суммы — целые тыйыны (как ``appointment.price_total_tiyin``). Долг считается
по приёму: ``outstanding = price_total − Σплатежей + Σвозвратов``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.shared.db.enums import PaymentKind, PaymentMethod


@dataclass(frozen=True, slots=True)
class PaymentRecord:
    """Кассовая операция (платёж или возврат) с данными для журнала."""

    id: uuid.UUID
    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    amount_tiyin: int
    method: PaymentMethod
    kind: PaymentKind
    recorded_by_membership_id: uuid.UUID | None
    recorded_by_name: str | None
    comment: str | None
    created_at: datetime
    #: Для возврата — исходный платёж; у платежа ``None``.
    refunds_payment_id: uuid.UUID | None = None
    #: Сколько по этому платежу уже возвращено (возвратов может быть несколько).
    refunded_tiyin: int = 0
    #: Врач приёма, который оплачивает операция: по нему касса режется «по врачу».
    #: У операции своего врача нет — он у приёма; у приёма без врача — ``None``.
    doctor_membership_id: uuid.UUID | None = None
    doctor_name: str | None = None

    @property
    def refundable_tiyin(self) -> int:
        """Сколько ещё можно вернуть по этой операции (у возврата — ноль).

        Возврат частичный и многократный, поэтому «можно ли вернуть» — это не
        флаг, а остаток: клиенту нужно знать, какую сумму подставить в форму.
        """
        if self.kind is not PaymentKind.PAYMENT:
            return 0
        return max(0, self.amount_tiyin - self.refunded_tiyin)


@dataclass(frozen=True, slots=True)
class PaymentsPage:
    """Страница журнала кассы (offset-пагинация)."""

    items: list[PaymentRecord]
    total_count: int
    current_page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class MethodTotals:
    """Итог по способу оплаты: приход, возвраты и нетто."""

    method: PaymentMethod
    income_tiyin: int
    refund_tiyin: int
    net_tiyin: int


@dataclass(frozen=True, slots=True)
class CashSummary:
    """Сводка кассы за период."""

    timezone: str
    by_method: list[MethodTotals]
    total_income_tiyin: int
    total_refund_tiyin: int
    net_tiyin: int


@dataclass(frozen=True, slots=True)
class AppointmentDebt:
    """Долг по завершённому приёму: сколько начислено, оплачено и осталось."""

    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    doctor_membership_id: uuid.UUID | None
    scheduled_at: datetime
    price_total_tiyin: int
    paid_tiyin: int
    outstanding_tiyin: int


@dataclass(frozen=True, slots=True)
class DebtsList:
    """Список долгов и их общая сумма."""

    timezone: str
    items: list[AppointmentDebt]
    total_outstanding_tiyin: int


@dataclass(frozen=True, slots=True)
class AppointmentBilling:
    """Биллинговое состояние приёма — вход для проведения платежа/возврата."""

    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_membership_id: uuid.UUID | None
    price_total_tiyin: int
    paid_tiyin: int

    @property
    def outstanding_tiyin(self) -> int:
        """Остаток к оплате (может быть 0, если оплачено полностью)."""
        return self.price_total_tiyin - self.paid_tiyin
