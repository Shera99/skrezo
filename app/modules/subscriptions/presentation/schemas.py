"""Схемы модуля подписки клиники."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.modules.subscriptions.domain.status import SubscriptionView
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import SubscriptionStatus


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
            card_attached_at=s.card_attached_at,
            current_period_start=s.current_period_start,
            current_period_end=s.current_period_end,
            cancelled_at=s.cancelled_at,
        )
