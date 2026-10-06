"""Use case: что клиника может сделать со своим тарифом — для экрана «Подписка».

По каждому публичному пакету: можно ли на него перейти и сколько это стоит
сейчас (доплата за остаток оплаченного периода). Плюс почему смена закрыта
вовсе: соло, спецплан, подписка закрыта Skrezo — и открытая заявка на условия
сверх тарифов, если клиника её уже отправила.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.payments.application.services.pricing import SubscriptionPricing, TariffCard
from app.modules.payments.domain.plans import (
    BLOCK_CUSTOM_PLAN,
    BLOCK_SOLO,
    BLOCK_SUBSCRIPTION_BLOCKED,
    BLOCK_SUBSCRIPTION_CANCELLED,
    UNAVAILABLE_CURRENT,
    UNAVAILABLE_SEATS_IN_USE,
    remaining_days,
    upgrade_charge,
)
from app.modules.payments.infrastructure.repositories.plan_repository import PlanRepository
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.modules.tariffs.domain.pricing import PackageDef
from app.shared.db.enums import ClinicType, SubscriptionStatus
from app.shared.db.models.plan_request import PlanRequest
from app.shared.db.models.subscription import Subscription


@dataclass(frozen=True, slots=True)
class PackageOption:
    """Публичный пакет глазами клиники."""

    package: PackageDef
    current: bool
    available: bool
    #: Почему перейти нельзя: current | seats_in_use | причина закрытой смены.
    unavailable_reason: str | None
    #: Доплата сейчас, если перейти; 0 — без доплаты.
    change_cost_tiyin: int


@dataclass(frozen=True, slots=True)
class PlanOptions:
    card: TariffCard
    seats_used: int
    seats_limit: int | None
    #: Почему смена пакета закрыта целиком; ``None`` — открыта.
    change_blocked_reason: str | None
    options: list[PackageOption]
    open_request: PlanRequest | None


def blocked_reason(card: TariffCard, subscription: Subscription | None) -> str | None:
    """Почему клиника не может сменить пакет сама; ``None`` — может."""
    if card.clinic_type is ClinicType.SOLO:
        return BLOCK_SOLO
    if card.custom_plan is not None:
        return BLOCK_CUSTOM_PLAN
    if subscription is not None and subscription.status is SubscriptionStatus.BLOCKED:
        return BLOCK_SUBSCRIPTION_BLOCKED
    if subscription is not None and subscription.status is SubscriptionStatus.CANCELLED:
        return BLOCK_SUBSCRIPTION_CANCELLED
    return None


def paid_days_left(subscription: Subscription | None, now: datetime) -> int:
    """Дней оплаченного периода впереди; на триале и без оплаты — 0 (доплаты нет)."""
    if subscription is None or subscription.status is not SubscriptionStatus.ACTIVE:
        return 0
    return remaining_days(now, subscription.current_period_end)


class GetPlanOptionsUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self._pricing = SubscriptionPricing(session)
        self._subscriptions = SubscriptionRepository(session)
        self._plans = PlanRepository(session)

    async def execute(self, clinic_id: uuid.UUID) -> PlanOptions:
        card = await self._pricing.card(clinic_id)
        subscription = await self._subscriptions.by_clinic(clinic_id)
        used, limit = await self._subscriptions.seats(clinic_id)
        reason = blocked_reason(card, subscription)
        days = paid_days_left(subscription, datetime.now(UTC))
        options: list[PackageOption] = []
        for package in card.packages:
            why: str | None
            if reason is not None:
                why = reason
            elif limit == package.up_to:
                why = UNAVAILABLE_CURRENT
            elif used > package.up_to:
                why = UNAVAILABLE_SEATS_IN_USE
            else:
                why = None
            options.append(
                PackageOption(
                    package=package,
                    current=package.name == card.current_package,
                    available=why is None,
                    unavailable_reason=why,
                    change_cost_tiyin=(
                        upgrade_charge(package.price - card.monthly_tiyin, days)
                        if why is None
                        else 0
                    ),
                )
            )
        return PlanOptions(
            card=card,
            seats_used=used,
            seats_limit=limit,
            change_blocked_reason=reason,
            options=options,
            open_request=await self._plans.open_request(clinic_id),
        )
