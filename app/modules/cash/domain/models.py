"""Доменные структуры кассы: платёж, страница журнала, сводка, долг по приёму.

Все суммы — целые сомы (как ``appointment.price_total``). Долг считается по
приёму: ``outstanding = price_total − Σплатежей + Σвозвратов``.
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
    amount: int
    method: PaymentMethod
    kind: PaymentKind
    recorded_by_membership_id: uuid.UUID | None
    recorded_by_name: str | None
    comment: str | None
    created_at: datetime


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
    income: int
    refund: int
    net: int


@dataclass(frozen=True, slots=True)
class CashSummary:
    """Сводка кассы за период."""

    timezone: str
    by_method: list[MethodTotals]
    total_income: int
    total_refund: int
    net: int


@dataclass(frozen=True, slots=True)
class AppointmentDebt:
    """Долг по завершённому приёму: сколько начислено, оплачено и осталось."""

    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    doctor_membership_id: uuid.UUID | None
    scheduled_at: datetime
    price_total: int
    paid: int
    outstanding: int


@dataclass(frozen=True, slots=True)
class DebtsList:
    """Список долгов и их общая сумма."""

    timezone: str
    items: list[AppointmentDebt]
    total_outstanding: int


@dataclass(frozen=True, slots=True)
class AppointmentBilling:
    """Биллинговое состояние приёма — вход для проведения платежа/возврата."""

    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_membership_id: uuid.UUID | None
    price_total: int
    paid: int

    @property
    def outstanding(self) -> int:
        """Остаток к оплате (может быть 0, если оплачено полностью)."""
        return self.price_total - self.paid
