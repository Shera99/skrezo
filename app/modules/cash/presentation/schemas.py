"""Схемы модуля кассы (camelCase наружу).

Все суммы — целые тыйыны (1/100 сома). Времена операций — aware-datetime в
таймзоне клиники (поле ``timezone`` в списках/сводке), чтобы фронт не
пересчитывал.
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
    amount_tiyin: int = Field(
        gt=0, description="Сумма платежа в тыйынах (1/100 сома, > 0)", examples=[150000]
    )
    method: PaymentMethod = Field(description="Способ оплаты", examples=["cash"])
    comment: str | None = Field(default=None, max_length=500, description="Комментарий")


class RefundPaymentRequest(CamelModel):
    """Возврат по платежу: сумма (необязательна) и пояснение."""

    amount_tiyin: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Сумма возврата в тыйынах. Не передана — возвращается весь остаток "
            "платежа (`refundableTiyin`). Больше остатка → 422"
        ),
        examples=[50000],
    )
    comment: str | None = Field(
        default=None,
        max_length=500,
        description="За что вернули (например, переделанная услуга)",
        examples=["Переделали чистку по гарантии"],
    )


class PaymentItem(CamelModel):
    """Кассовая операция в журнале."""

    id: uuid.UUID
    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str = Field(examples=["Петров Пётр"])
    amount_tiyin: int = Field(description="Сумма в тыйынах", examples=[150000])
    method: PaymentMethod
    kind: PaymentKind = Field(description="payment — платёж, refund — возврат")
    recorded_by_membership_id: uuid.UUID | None = Field(default=None, description="Кассир")
    recorded_by_name: str | None = Field(default=None, examples=["Иванов Иван"])
    doctor_membership_id: uuid.UUID | None = Field(
        default=None, description="Врач приёма, который оплачивает операция (id членства)"
    )
    doctor_name: str | None = Field(default=None, examples=["Сидорова Анна"])
    comment: str | None = None
    created_at: datetime = Field(examples=["2026-07-20T14:30:00+06:00"])
    refunds_payment_id: uuid.UUID | None = Field(
        default=None, description="У возврата — платёж, который он сторнирует"
    )
    refunded_tiyin: int = Field(
        default=0, description="Сколько уже возвращено по этому платежу", examples=[50000]
    )
    refundable_tiyin: int = Field(
        default=0,
        description="Сколько ещё можно вернуть (у возврата — 0)",
        examples=[100000],
    )

    @classmethod
    def from_record(cls, r: PaymentRecord) -> PaymentItem:
        return cls(
            id=r.id,
            appointment_id=r.appointment_id,
            patient_id=r.patient_id,
            patient_name=r.patient_name,
            amount_tiyin=r.amount_tiyin,
            method=r.method,
            kind=r.kind,
            recorded_by_membership_id=r.recorded_by_membership_id,
            recorded_by_name=r.recorded_by_name,
            doctor_membership_id=r.doctor_membership_id,
            doctor_name=r.doctor_name,
            comment=r.comment,
            created_at=r.created_at,
            refunds_payment_id=r.refunds_payment_id,
            refunded_tiyin=r.refunded_tiyin,
            refundable_tiyin=r.refundable_tiyin,
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
    income_tiyin: int = Field(description="Приход в тыйынах")
    refund_tiyin: int = Field(description="Возвраты в тыйынах")
    net_tiyin: int = Field(description="Нетто (приход − возвраты), тыйыны")

    @classmethod
    def from_totals(cls, t: MethodTotals) -> MethodTotalsOut:
        return cls(
            method=t.method,
            income_tiyin=t.income_tiyin,
            refund_tiyin=t.refund_tiyin,
            net_tiyin=t.net_tiyin,
        )


class CashSummaryResponse(CamelModel):
    """Сводка кассы за период."""

    timezone: str = Field(examples=["Asia/Bishkek"])
    by_method: list[MethodTotalsOut]
    total_income_tiyin: int
    total_refund_tiyin: int
    net_tiyin: int

    @classmethod
    def from_summary(cls, s: CashSummary) -> CashSummaryResponse:
        return cls(
            timezone=s.timezone,
            by_method=[MethodTotalsOut.from_totals(m) for m in s.by_method],
            total_income_tiyin=s.total_income_tiyin,
            total_refund_tiyin=s.total_refund_tiyin,
            net_tiyin=s.net_tiyin,
        )


class DebtItem(CamelModel):
    """Долг по завершённому приёму."""

    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str = Field(examples=["Петров Пётр"])
    doctor_membership_id: uuid.UUID | None = None
    scheduled_at: datetime = Field(examples=["2026-07-20T09:00:00+06:00"])
    price_total_tiyin: int = Field(description="Начислено по приёму, тыйыны")
    paid_tiyin: int = Field(description="Оплачено нетто, тыйыны")
    outstanding_tiyin: int = Field(description="Остаток долга, тыйыны")

    @classmethod
    def from_debt(cls, d: AppointmentDebt) -> DebtItem:
        return cls(
            appointment_id=d.appointment_id,
            patient_id=d.patient_id,
            patient_name=d.patient_name,
            doctor_membership_id=d.doctor_membership_id,
            scheduled_at=d.scheduled_at,
            price_total_tiyin=d.price_total_tiyin,
            paid_tiyin=d.paid_tiyin,
            outstanding_tiyin=d.outstanding_tiyin,
        )


class DebtsResponse(CamelModel):
    """Долги клиники и их общая сумма."""

    timezone: str = Field(examples=["Asia/Bishkek"])
    items: list[DebtItem]
    total_outstanding_tiyin: int = Field(description="Сумма всех остатков, тыйыны")

    @classmethod
    def from_list(cls, debts: DebtsList) -> DebtsResponse:
        return cls(
            timezone=debts.timezone,
            items=[DebtItem.from_debt(d) for d in debts.items],
            total_outstanding_tiyin=debts.total_outstanding_tiyin,
        )
