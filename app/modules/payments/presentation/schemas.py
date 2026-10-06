"""Схемы платежей: журнал Skrezo и оплата подписки клиникой (FreedomPay)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from app.modules.payments.application.services.pricing import TariffCard
from app.modules.payments.application.use_cases.change_package import PackageChange
from app.modules.payments.application.use_cases.plan_options import PlanOptions
from app.modules.payments.domain.checkout import ReturnMode
from app.modules.payments.infrastructure.repositories.plan_repository import PlanRequestRow
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import ClinicType
from app.shared.db.models.clinic_custom_plan import ClinicCustomPlan
from app.shared.db.models.plan_request import PlanRequest


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


class TariffPackageOut(CamelModel):
    """Пакет тарифа клиники: до скольких мест и за сколько в месяц."""

    name: str = Field(examples=["Практика"])
    up_to: int = Field(
        description="Покрывает клинику до этого числа мест включительно", examples=[6]
    )
    price_tiyin: int = Field(description="Цена пакета в месяц, тыйыны", examples=[500000])
    current: bool = Field(description="Клиника сейчас в этом пакете (по числу мест)")
    available: bool = Field(
        default=False,
        description=(
            "Можно перейти на пакет самой (`PUT /clinic/subscription/package`). У текущего "
            "пакета `true`, если лимит мест ниже его границы: переход откроет места пакета "
            "без доплаты"
        ),
    )
    unavailable_reason: str | None = Field(
        default=None,
        description=(
            "Почему перейти нельзя: `current` — уже на нём, `seats_in_use` — сотрудников "
            "больше мест пакета, либо причина закрытой смены (`changeBlockedReason`)"
        ),
        examples=["seats_in_use"],
    )
    change_cost_tiyin: int = Field(
        default=0,
        description=(
            "Доплата сейчас, если перейти: разница цен за остаток оплаченного периода; "
            "0 — без доплаты (пакет дешевле, триал, период не оплачен)"
        ),
        examples=[133334],
    )


class CustomPlanOut(CamelModel):
    """Индивидуальный план клиники от Skrezo."""

    title: str = Field(examples=["Клиника+ 14 мест"])
    seats: int = Field(description="Мест по плану — лимит сотрудников", examples=[14])
    monthly_tiyin: int = Field(description="Цена в месяц, тыйыны", examples=[1100000])


class PlanRequestOut(CamelModel):
    """Заявка клиники на условия сверх публичных тарифов."""

    id: uuid.UUID
    seats: int = Field(description="Сколько мест нужно", examples=[14])
    comment: str | None = None
    status: Literal["new", "done", "declined"] = Field(
        description="`new` — ждёт Skrezo, `done` — договорились, `declined` — отказ"
    )
    submitted_at: datetime
    resolution_note: str | None = None
    resolved_at: datetime | None = None

    @classmethod
    def from_model(cls, r: PlanRequest) -> PlanRequestOut:
        return cls(
            id=r.id,
            seats=r.seats,
            comment=r.comment,
            status=r.status,
            submitted_at=r.submitted_at,
            resolution_note=r.resolution_note,
            resolved_at=r.resolved_at,
        )


class TariffPackagesOut(CamelModel):
    """Тариф клиники: пакеты, доплата за место и текущая цена.

    Цена считается тем же правилом, что и `GET /clinic/subscription/price`: мест —
    лимит по тарифу либо занятые, если занято больше. У соло пакетов нет — цена
    фиксированная (`monthlyTiyin`).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "clinic",
                "packages": [
                    {"name": "Старт", "upTo": 3, "priceTiyin": 300000, "current": False},
                    {"name": "Практика", "upTo": 6, "priceTiyin": 500000, "current": True},
                ],
                "overPriceTiyin": 80000,
                "seats": 5,
                "monthlyTiyin": 500000,
                "currentPackage": "Практика",
                "currency": "KGS",
            }
        }
    )

    type: ClinicType = Field(description="clinic — пакеты по местам; solo — фиксированная цена")
    packages: list[TariffPackageOut] = Field(description="По возрастанию мест; у соло — пусто")
    over_price_tiyin: int | None = Field(
        default=None,
        description="Доплата за каждое место сверх последнего пакета («Сеть»); у соло — null",
        examples=[80000],
    )
    seats: int | None = Field(
        default=None, description="Мест, по которым считается цена; у соло — null", examples=[5]
    )
    monthly_tiyin: int = Field(description="Цена в месяц для клиники сейчас", examples=[500000])
    current_package: str | None = Field(
        default=None,
        description="Название текущего пакета; null — соло или мест больше последнего пакета",
        examples=["Практика"],
    )
    currency: str = Field(default="KGS")
    seats_used: int | None = Field(
        default=None, description="Занято мест: активные сотрудники кроме владельца", examples=[4]
    )
    seats_limit: int | None = Field(
        default=None, description="Лимит мест клиники сейчас", examples=[6]
    )
    custom_plan: CustomPlanOut | None = Field(
        default=None,
        description=(
            "Индивидуальный план от Skrezo: заполнен — цена и места из него, пакеты только "
            "для справки, сменить план можно через Skrezo"
        ),
    )
    can_change: bool = Field(
        default=False, description="Может ли клиника сама перейти на другой пакет"
    )
    change_blocked_reason: str | None = Field(
        default=None,
        description=(
            "Почему смена закрыта: `solo`, `custom_plan_active`, `subscription_blocked`, "
            "`subscription_cancelled`"
        ),
    )
    open_request: PlanRequestOut | None = Field(
        default=None,
        description="Открытая заявка на условия сверх тарифов (`POST …/plan-request`)",
    )

    @classmethod
    def from_options(cls, options: PlanOptions) -> TariffPackagesOut:
        base = cls.from_card(options.card)
        by_name = {o.package.name: o for o in options.options}
        packages = [
            p.model_copy(
                update={
                    "available": by_name[p.name].available,
                    "unavailable_reason": by_name[p.name].unavailable_reason,
                    "change_cost_tiyin": by_name[p.name].change_cost_tiyin,
                }
            )
            for p in base.packages
        ]
        custom = options.card.custom_plan
        return base.model_copy(
            update={
                "packages": packages,
                "seats_used": options.seats_used,
                "seats_limit": options.seats_limit,
                "custom_plan": (
                    CustomPlanOut(
                        title=custom.title, seats=custom.seats, monthly_tiyin=custom.monthly_tiyin
                    )
                    if custom is not None
                    else None
                ),
                "can_change": options.change_blocked_reason is None,
                "change_blocked_reason": options.change_blocked_reason,
                "open_request": (
                    PlanRequestOut.from_model(options.open_request)
                    if options.open_request is not None
                    else None
                ),
            }
        )

    @classmethod
    def from_card(cls, card: TariffCard) -> TariffPackagesOut:
        return cls(
            type=card.clinic_type,
            packages=[
                TariffPackageOut(
                    name=p.name,
                    up_to=p.up_to,
                    price_tiyin=p.price,
                    current=p.name == card.current_package,
                )
                for p in card.packages
            ],
            over_price_tiyin=card.over_price_tiyin,
            seats=card.seats,
            monthly_tiyin=card.monthly_tiyin,
            current_package=card.current_package,
        )


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


# ── Смена пакета и заявки клиники ───────────────────────────────────────────


class ChangePackageRequest(CamelModel):
    """Перейти на публичный пакет: какой — по его границе мест."""

    model_config = ConfigDict(json_schema_extra={"example": {"upTo": 10}})

    up_to: int = Field(
        ge=1, description="`upTo` пакета из `GET /clinic/subscription/packages`", examples=[10]
    )


class PackageChangeOut(CamelModel):
    """Итог перехода и тариф после него."""

    from_package: str | None = Field(default=None, examples=["Практика"])
    to_package: str = Field(examples=["Клиника"])
    seats_limit: int = Field(description="Новый лимит мест", examples=[10])
    monthly_tiyin: int = Field(description="Цена в месяц со следующей оплаты", examples=[800000])
    charged_tiyin: int = Field(
        description="Списано с баланса сейчас: доплата за остаток периода", examples=[600000]
    )
    charged_days: int = Field(description="За сколько дней остатка взята доплата", examples=[18])
    balance_tiyin: int = Field(description="Баланс после перехода", examples=[400000])
    plan: TariffPackagesOut = Field(description="Тариф клиники после перехода")

    @classmethod
    def build(cls, change: PackageChange, plan: TariffPackagesOut) -> PackageChangeOut:
        return cls(
            from_package=change.from_package,
            to_package=change.to_package,
            seats_limit=change.seats_limit,
            monthly_tiyin=change.monthly_tiyin,
            charged_tiyin=change.charged_tiyin,
            charged_days=change.charged_days,
            balance_tiyin=change.balance_tiyin,
            plan=plan,
        )


class PlanRequestCreate(CamelModel):
    """Нужно больше мест, чем в самом большом пакете, — или другие условия."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"seats": 14, "comment": "Открываем второй филиал"}}
    )

    seats: int = Field(ge=1, le=1000, description="Сколько мест нужно", examples=[14])
    comment: str | None = Field(default=None, max_length=1000)


# ── Спецплан и заявки: админка Skrezo ──────────────────────────────────────


class CustomPlanRequest(CamelModel):
    """Индивидуальные условия клиники: мест и цена в месяц."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "Клиника+ 14 мест",
                "seats": 14,
                "priceTiyin": 1100000,
                "note": "Договорились 06.10: 2 филиала, цена на год",
            }
        }
    )

    title: str = Field(min_length=1, max_length=120, description="Название плана для клиники")
    seats: int = Field(ge=1, le=1000, description="Мест — станет лимитом сотрудников")
    price_tiyin: int = Field(gt=0, description="Цена в месяц, тыйыны")
    note: str | None = Field(
        default=None, max_length=2000, description="Внутренняя заметка Skrezo; клиника не видит"
    )


class AdminCustomPlanOut(CamelModel):
    """Спецплан клиники в админке."""

    title: str
    seats: int
    price_tiyin: int
    note: str | None = None
    set_by: uuid.UUID | None = None
    updated_at: datetime

    @classmethod
    def from_model(cls, plan: ClinicCustomPlan) -> AdminCustomPlanOut:
        return cls(
            title=plan.title,
            seats=plan.seats,
            price_tiyin=plan.price_tiyin,
            note=plan.note,
            set_by=plan.set_by,
            updated_at=plan.updated_at,
        )


class AdminPlanRequestOut(PlanRequestOut):
    """Заявка в очереди админки: чья и от кого."""

    clinic_id: uuid.UUID
    clinic_name: str = Field(examples=["Стоматология Улыбка"])
    requested_by: uuid.UUID | None = None

    @classmethod
    def from_row(cls, row: PlanRequestRow) -> AdminPlanRequestOut:
        base = PlanRequestOut.from_model(row.request)
        return cls(
            **base.model_dump(),
            clinic_id=row.request.clinic_id,
            clinic_name=row.clinic_name,
            requested_by=row.request.requested_by,
        )


class AdminPlanRequestsResponse(CamelModel):
    list: list[AdminPlanRequestOut]
    current_page: int
    page_size: int = 25
    total_count: int


class ResolvePlanRequest(CamelModel):
    """Закрыть заявку: договорились или отказ."""

    model_config = ConfigDict(json_schema_extra={"example": {"status": "declined"}})

    status: Literal["done", "declined"]
    note: str | None = Field(default=None, max_length=2000)
