"""Use case: клиника сама переходит на другой публичный пакет.

Пакет — это лимит мест: переход меняет ``clinic.seats_limit`` на границу пакета.
Дороже — сразу, с доплатой за остаток оплаченного периода с баланса (не хватает
— 402 с суммами, ничего не меняется). Дешевле — сразу и без возврата, если
сотрудников не больше мест нового пакета. Новая цена в любом случае действует
со следующей оплаты (см. ``domain/plans``).

Всё делается в общей очереди денежных изменений клиники — под блокировкой
кошелька, как продление и колбэк оплаты: двойной клик не спишет доплату дважды,
а смена тарифа не встанет посреди ночного продления.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    NotFoundError,
    PaymentRequiredError,
    ValidationAppError,
)
from app.modules.payments.application.services.ledger_service import (
    LedgerService,
    ensure_renewable,
)
from app.modules.payments.application.services.pricing import SubscriptionPricing
from app.modules.payments.application.use_cases.plan_options import paid_days_left
from app.modules.payments.domain.ledger import (
    KIND_DEBIT,
    SOURCE_PLAN_CHANGE,
    InsufficientFundsError,
)
from app.modules.payments.domain.plans import upgrade_charge
from app.modules.payments.infrastructure.repositories.plan_repository import PlanRepository
from app.modules.push.domain.events import format_som
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.db.enums import ClinicType
from app.shared.notifications.notifier import Notifier

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PackageChange:
    from_package: str | None
    to_package: str
    seats_limit: int
    #: Новая цена в месяц — со следующей оплаты.
    monthly_tiyin: int
    charged_tiyin: int
    #: За сколько дней остатка периода взята доплата.
    charged_days: int
    balance_tiyin: int


class ChangePackageUseCase:
    def __init__(self, session: AsyncSession, notifier: Notifier | None = None) -> None:
        self._session = session
        self._ledger = LedgerService(session)
        self._pricing = SubscriptionPricing(session)
        self._plans = PlanRepository(session)
        self._subscriptions = SubscriptionRepository(session)
        self._notifier = notifier

    async def execute(
        self, clinic_id: uuid.UUID, actor_id: uuid.UUID | None, up_to: int
    ) -> PackageChange:
        wallet, subscription = await self._ledger.lock_for_change(clinic_id)
        ensure_renewable(subscription)
        clinic = await self._plans.clinic_fresh(clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        if clinic.type is ClinicType.SOLO:
            raise ConflictError(
                "У тарифа соло нет пакетов — сменить тариф поможет Skrezo",
                code="plan_change_not_available",
            )
        if await self._plans.custom_plan(clinic_id) is not None:
            raise ConflictError(
                "У клиники индивидуальный план — по изменениям напишите в Skrezo",
                code="custom_plan_active",
            )
        card = await self._pricing.card(clinic_id)
        target = next((p for p in card.packages if p.up_to == up_to), None)
        if target is None:
            raise ValidationAppError(
                "Такого пакета нет в действующем тарифе", code="package_not_found"
            )
        if clinic.seats_limit == target.up_to:
            raise ConflictError("Клиника уже на этом пакете", code="package_already_current")
        used, _ = await self._subscriptions.seats(clinic_id)
        if used > target.up_to:
            raise ConflictError(
                f"В пакете «{target.name}» мест {target.up_to}, а сотрудников {used} — "
                "сначала отключите лишних",
                code="seats_in_use",
                extra={"seatsUsed": used, "upTo": target.up_to},
            )

        now = datetime.now(UTC)
        days = paid_days_left(subscription, now)
        charge = upgrade_charge(target.price - card.monthly_tiyin, days)
        if charge:
            assert subscription is not None and subscription.current_period_end is not None
            end = subscription.current_period_end
            try:
                await self._ledger.post(
                    clinic_id,
                    kind=KIND_DEBIT,
                    source=SOURCE_PLAN_CHANGE,
                    amount_tiyin=charge,
                    description=(
                        f"Переход на «{target.name}»: доплата за {days} дн. до {end:%d.%m.%Y}"
                    ),
                    period=(now, end),
                    created_by=actor_id,
                )
            except InsufficientFundsError as exc:
                raise PaymentRequiredError(
                    "На балансе не хватает на доплату за переход — пополните баланс",
                    code="insufficient_balance",
                    extra={
                        "balanceTiyin": exc.balance_tiyin,
                        "amountTiyin": exc.amount_tiyin,
                        "shortfallTiyin": exc.shortfall_tiyin,
                    },
                ) from exc
        previous = card.current_package
        clinic.seats_limit = target.up_to
        await self._session.commit()

        logger.info(
            "subscription_package_changed",
            clinic_id=str(clinic_id),
            from_package=previous,
            to_package=target.name,
            charged=charge,
        )
        if self._notifier is not None:
            extra = f", доплата {format_som(charge)} сом" if charge else ""
            await self._notifier.notify_team(
                f"«{clinic.name}» сменила тариф: {previous or 'без пакета'} → {target.name}{extra}",
                event="subscription_package_changed",
            )
        return PackageChange(
            from_package=previous,
            to_package=target.name,
            seats_limit=target.up_to,
            monthly_tiyin=target.price,
            charged_tiyin=charge,
            charged_days=days if charge else 0,
            balance_tiyin=wallet.balance_tiyin,
        )
