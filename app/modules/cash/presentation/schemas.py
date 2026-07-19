"""Схемы модуля кассы (camelCase наружу).

Все суммы — целые сомы. Времена операций — aware-datetime в таймзоне клиники
(поле ``timezone`` в списках/сводке), чтобы фронт не пересчитывал.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.modules.cash.domain.models import (
    AppointmentDebt,
    CashSummary,
    DebtsList,
    MethodTotals,
    PaymentRecord,
    PaymentsPage,
)
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import PaymentKind, PaymentMethod


class RecordPaymentRequest(CamelModel):
    """Проведение платежа по приёму."""

    appointment_id: uuid.UUID = Field(description="Приём, за который платят")
    amount: int = Field(gt=0, description="Сумма платежа в сомах (целое, > 0)", examples=[1500])
    method: PaymentMethod = Field(description="Способ оплаты", examples=["cash"])
    comment: str | None = Field(default=None, max_length=500, description="Комментарий")


class PaymentItem(CamelModel):
    """Кассовая операция в журнале."""

    id: uuid.UUID
    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str = Field(examples=["Петров Пётр"])
    amount: int = Field(description="Сумма в сомах", examples=[1500])
    method: PaymentMethod
    kind: PaymentKind = Field(description="payment — платёж, refund — возврат")
    recorded_by_membership_id: uuid.UUID | None = Field(default=None, description="Кассир")
    recorded_by_name: str | None = Field(default=None, examples=["Иванов Иван"])
    comment: str | None = None
    created_at: datetime = Field(examples=["2026-07-20T14:30:00+06:00"])

    @classmethod
    def from_record(cls, r: PaymentRecord) -> PaymentItem:
        return cls(
            id=r.id,
            appointment_id=r.appointment_id,
            patient_id=r.patient_id,
            patient_name=r.patient_name,
            amount=r.amount,
            method=r.method,
            kind=r.kind,
            recorded_by_membership_id=r.recorded_by_membership_id,
            recorded_by_name=r.recorded_by_name,
            comment=r.comment,
            created_at=r.created_at,
        )


class PaymentsResponse(CamelModel):
    """Журнал кассы (offset-пагинация)."""

    list: list[PaymentItem]
    total_count: int
    current_page: int
    page_size: int

    @classmethod
    def from_page(cls, page: PaymentsPage) -> PaymentsResponse:
        return cls(
            list=[PaymentItem.from_record(r) for r in page.items],
            total_count=page.total_count,
            current_page=page.current_page,
            page_size=page.page_size,
        )


class MethodTotalsOut(CamelModel):
    """Итог по способу оплаты."""

    method: PaymentMethod
    income: int = Field(description="Приход в сомах")
    refund: int = Field(description="Возвраты в сомах")
    net: int = Field(description="Нетто (приход − возвраты)")

    @classmethod
    def from_totals(cls, t: MethodTotals) -> MethodTotalsOut:
        return cls(method=t.method, income=t.income, refund=t.refund, net=t.net)


class CashSummaryResponse(CamelModel):
    """Сводка кассы за период."""

    timezone: str = Field(examples=["Asia/Bishkek"])
    by_method: list[MethodTotalsOut]
    total_income: int
    total_refund: int
    net: int

    @classmethod
    def from_summary(cls, s: CashSummary) -> CashSummaryResponse:
        return cls(
            timezone=s.timezone,
            by_method=[MethodTotalsOut.from_totals(m) for m in s.by_method],
            total_income=s.total_income,
            total_refund=s.total_refund,
            net=s.net,
        )


class DebtItem(CamelModel):
    """Долг по завершённому приёму."""

    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str = Field(examples=["Петров Пётр"])
    doctor_membership_id: uuid.UUID | None = None
    scheduled_at: datetime = Field(examples=["2026-07-20T09:00:00+06:00"])
    price_total: int = Field(description="Начислено по приёму, сомы")
    paid: int = Field(description="Оплачено нетто, сомы")
    outstanding: int = Field(description="Остаток долга, сомы")

    @classmethod
    def from_debt(cls, d: AppointmentDebt) -> DebtItem:
        return cls(
            appointment_id=d.appointment_id,
            patient_id=d.patient_id,
            patient_name=d.patient_name,
            doctor_membership_id=d.doctor_membership_id,
            scheduled_at=d.scheduled_at,
            price_total=d.price_total,
            paid=d.paid,
            outstanding=d.outstanding,
        )


class DebtsResponse(CamelModel):
    """Долги клиники и их общая сумма."""

    timezone: str = Field(examples=["Asia/Bishkek"])
    items: list[DebtItem]
    total_outstanding: int = Field(description="Сумма всех остатков, сомы")

    @classmethod
    def from_list(cls, debts: DebtsList) -> DebtsResponse:
        return cls(
            timezone=debts.timezone,
            items=[DebtItem.from_debt(d) for d in debts.items],
            total_outstanding=debts.total_outstanding,
        )
