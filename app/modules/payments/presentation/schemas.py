"""Схемы платежей: журнал Skrezo и оплата подписки клиникой (FreedomPay)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, model_validator

from app.modules.payments.domain.checkout import ReturnMode
from app.shared.api.schemas import CamelModel


class PaymentSummary(CamelModel):
    period_label: str
    success_sum_tiyin: int = 0
    success_count: int = 0
    failed_count: int = 0
    failed_sum_tiyin: int = 0
    refund_sum_tiyin: int = 0  # отрицательная
    refund_count: int = 0
    net_tiyin: int = 0  # успешно − возвраты


class PaymentRow(CamelModel):
    id: str
    date: datetime
    client: str
    client_type: str = Field(description="solo | clinic")
    type: str = Field(description="«Оплата подписки» | «Возврат»")
    period: str = Field(description="Оплаченный период «дд.мм.гггг — дд.мм.гггг» или «—»")
    amount_tiyin: int  # отрицательная — возврат
    status: str = Field(description="success | failed | refund | pending")
    method: str = Field(description="FreedomPay")
    txn_id: str = Field(description="Идентификатор платежа у провайдера")


class PaymentsBundle(CamelModel):
    summary: PaymentSummary
    rows: list[PaymentRow] = Field(default_factory=list)
    total_count: int = 0


class RefundRequest(CamelModel):
    reason: str = Field(min_length=1, description="Причина возврата")


class StartPaymentRequest(CamelModel):
    """Что оплатить: `months` — подписка по тарифу, `amountTiyin` — пополнение баланса.

    `returnMode` — как вернуть человека в кабинет после оплаты: `auto` — сразу
    (компьютер), `button` — кнопкой «Вернуться» на странице провайдера
    (телефон). Не передано — кнопкой.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"months": 3, "returnMode": "auto"},
                {"amountTiyin": 300000, "returnMode": "button"},
            ]
        }
    )

    months: int | None = Field(default=None, ge=1, le=12, examples=[1])
    amount_tiyin: int | None = Field(
        default=None, gt=0, description="Пополнение баланса, тыйыны (не меньше минимума)"
    )
    return_mode: ReturnMode | None = Field(
        default=None,
        description=(
            "Возврат на `{CABINET_URL}/pay?payment=<id>` после оплаты: `auto` — сразу, "
            "без кнопки (компьютер), `button` — кнопкой «Вернуться» (телефон). "
            "Не передано — кнопкой"
        ),
        examples=["auto"],
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> StartPaymentRequest:
        if (self.months is None) == (self.amount_tiyin is None):
            raise ValueError("Укажите ровно одно: months или amountTiyin")
        return self


class PayFromBalanceRequest(CamelModel):
    months: int = Field(ge=1, le=12, examples=[1])


class AutopayRequest(CamelModel):
    enabled: bool = Field(description="Продлевать подписку с баланса автоматически")


class LedgerEntryOut(CamelModel):
    """Строка журнала операций клиники."""

    id: uuid.UUID
    kind: str = Field(description="credit — приход, debit — расход")
    source: str = Field(
        description="freedompay_payment | manual_topup | subscription_charge | refund | adjustment"
    )
    amount_tiyin: int
    balance_after_tiyin: int = Field(description="Остаток после операции")
    description: str
    payment_id: uuid.UUID | None = None
    period_start: datetime | None = None
    period_end: datetime | None = None
    created_at: datetime

    @classmethod
    def from_orm_entry(cls, e: Any) -> LedgerEntryOut:
        return cls(
            id=e.id,
            kind=e.kind,
            source=e.source,
            amount_tiyin=e.amount_tiyin,
            balance_after_tiyin=e.balance_after_tiyin,
            description=e.description,
            payment_id=e.payment_id,
            period_start=e.period_start,
            period_end=e.period_end,
            created_at=e.created_at,
        )


class LedgerResponse(CamelModel):
    balance_tiyin: int = Field(description="Текущий остаток на балансе клиники")
    autopay_enabled: bool
    list: list[LedgerEntryOut]
    current_page: int
    page_size: int = 25
    total_count: int


class AdminLedgerEntryRequest(CamelModel):
    """Ручная операция Skrezo: приход (перевод на счёт) или корректировка."""

    kind: str = Field(pattern="^(credit|debit)$", examples=["credit"])
    amount_tiyin: int = Field(gt=0, examples=[3000000])
    comment: str = Field(min_length=3, max_length=500, examples=["Перевод по счёту №12"])


class PriceQuoteOut(CamelModel):
    """Цена по тарифу до перехода к оплате."""

    monthly_tiyin: int = Field(description="Цена в месяц по тарифу клиники", examples=[300000])
    months: int
    total_tiyin: int = Field(examples=[300000])
    seats: int | None = Field(default=None, description="Мест по тарифу (клиника); null — соло")
    currency: str = Field(default="KGS")
    available: bool = Field(description="Онлайн-оплата подключена (реквизиты провайдера заданы)")


class ClinicPaymentOut(CamelModel):
    """Платёж за подписку глазами кабинета."""

    id: uuid.UUID
    status: str = Field(description="pending | paid | failed | refunded")
    purpose: str = Field(default="subscription", description="subscription | topup")
    amount_tiyin: int
    currency: str
    months: int
    description: str
    redirect_url: str | None = Field(
        default=None, description="Страница оплаты провайдера — куда отправить владельца"
    )
    period_start: datetime | None = None
    period_end: datetime | None = None
    paid_at: datetime | None = None
    failure_code: str | None = None
    failure_description: str | None = None
    created_at: datetime

    @classmethod
    def from_orm_payment(cls, p: Any) -> ClinicPaymentOut:
        return cls(
            id=p.id,
            status=p.status,
            purpose=p.purpose,
            amount_tiyin=p.amount_tiyin,
            currency=p.currency,
            months=p.months,
            description=p.description,
            redirect_url=p.redirect_url if p.status == "pending" else None,
            period_start=p.period_start,
            period_end=p.period_end,
            paid_at=p.paid_at,
            failure_code=p.failure_code,
            failure_description=p.failure_description,
            created_at=p.created_at,
        )


class ClinicPaymentsResponse(CamelModel):
    list: list[ClinicPaymentOut]
    current_page: int
    page_size: int = 25
    total_count: int
