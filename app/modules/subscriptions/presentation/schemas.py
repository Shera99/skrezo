"""Схемы модуля подписки клиники."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import Field

from app.modules.subscriptions.domain.status import SubscriptionView, build_view
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.subscription import Subscription


class ClinicSubscriptionResponse(CamelModel):
    """Подписка клиники: статус, срок действия и остаток дней."""

    status: SubscriptionStatus = Field(
        description=(
            "`trial` — 30 дней бесплатно; `active` — оплачено; "
            "`awaiting_card`, `past_due`, `blocked` — доступ закрыт; "
            "`cancelled` — клиент ушёл"
        ),
        examples=["trial"],
    )
    is_trial: bool = Field(description="Клиника на бесплатном триале", examples=[True])
    trial_started_at: datetime = Field(examples=["2026-07-06T10:00:00+00:00"])
    trial_ends_at: datetime = Field(examples=["2026-08-05T10:00:00+00:00"])
    expires_at: datetime | None = Field(
        default=None,
        description=(
            "До какой даты действует доступ: на триале — конец триала, иначе — "
            "конец оплаченного периода (null, если период не открыт)"
        ),
        examples=["2026-08-05T10:00:00+00:00"],
    )
    days_left: int = Field(
        description="Целых суток до `expiresAt`; 0 — идёт последний день либо срок вышел",
        examples=[12],
    )
    expired: bool = Field(
        description="Срок вышел — доступ пора продлевать", examples=[False]
    )
    writes_blocked: bool = Field(
        description=(
            "Клиника в режиме ЧТЕНИЯ: мутирующие операции отвечают 403 "
            "`subscription_inactive`. Фронту — прятать кнопки записи, кассы и правок"
        ),
        examples=[False],
    )
    card_attached_at: datetime | None = Field(
        default=None, description="Когда привязана карта (гейт карты сейчас отключён)"
    )
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    cancelled_at: datetime | None = None

    @classmethod
    def from_view(cls, view: SubscriptionView) -> ClinicSubscriptionResponse:
        s = view.subscription
        return cls(
            status=s.status,
            is_trial=s.status is SubscriptionStatus.TRIAL,
            trial_started_at=s.trial_started_at,
            trial_ends_at=s.trial_ends_at,
            expires_at=view.expires_at,
            days_left=view.days_left,
            expired=view.expired,
            writes_blocked=view.writes_blocked,
            card_attached_at=s.card_attached_at,
            current_period_start=s.current_period_start,
            current_period_end=s.current_period_end,
            cancelled_at=s.cancelled_at,
        )


class ActivateSubscriptionRequest(CamelModel):
    """Отметка оплаты: до какой даты открыт доступ."""

    paid_until: datetime = Field(
        description="Конец оплаченного периода; по этой дате доступ и закроется",
        examples=["2026-09-09T00:00:00+00:00"],
    )
    period_start: datetime | None = Field(
        default=None,
        description="Начало периода; по умолчанию — момент отметки оплаты",
        examples=["2026-08-09T00:00:00+00:00"],
    )


class BlockSubscriptionRequest(CamelModel):
    """Ручная блокировка клиники командой Skrezo."""

    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Причина — попадёт в историю подписки",
        examples=["нарушение условий"],
    )


class AdminSubscriptionResponse(CamelModel):
    """Подписка после ручной операции — то же состояние, что видит клиника."""

    clinic_id: uuid.UUID
    status: SubscriptionStatus
    trial_ends_at: datetime
    current_period_start: datetime | None = None
    current_period_end: datetime | None = None
    writes_blocked: bool = Field(
        description="Клиника в режиме чтения прямо сейчас (считается по датам, не по статусу)"
    )

    @classmethod
    def from_subscription(cls, subscription: Subscription) -> AdminSubscriptionResponse:
        view = build_view(subscription, datetime.now(UTC))
        return cls(
            clinic_id=subscription.clinic_id,
            status=subscription.status,
            trial_ends_at=subscription.trial_ends_at,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            writes_blocked=view.writes_blocked,
        )
