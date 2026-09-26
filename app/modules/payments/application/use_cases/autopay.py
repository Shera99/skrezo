"""Автопродление подписки с баланса — автосписание без участия провайдера.

Ночью: у кого период кончается в ближайшие N дней (или уже кончился) и
включено автопродление — списать месяц с баланса и продлить. Не хватает —
один раз на период предупредить владельца, сколько положить (со ссылкой на
страницу оплаты). Триал с деньгами на балансе продлевается так же, за N дней
до его конца, и период начинается с конца триала; триал без денег задание не
трогает — о конце триала предупреждает своё задание.

Задел на будущее: когда провайдер откроет автосписание с карты, здесь же,
перед предупреждением, будет попытка списать недостающее с карты по профилю
``clinic_wallet.recurring_profile_id`` — баланс пополнится, продление пройдёт.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.payments.application.services.ledger_service import LedgerService, subscription_due
from app.modules.payments.application.services.pricing import SubscriptionPricing
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.modules.subscriptions.domain.messages import balance_low_text, team_balance_low_text
from app.shared.cabinet_links import pay_url
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_wallet import ClinicWallet
from app.shared.db.models.membership import Membership
from app.shared.db.models.role import Role
from app.shared.db.models.subscription import Subscription
from app.shared.notifications.notifier import Notifier

logger = structlog.get_logger(__name__)

#: Кого продлевать с баланса: действующую, истёкшую по оплате и триал.
#: Триал — потому что пополненный на триале баланс
#: списывается за ``AUTOPAY_DAYS_BEFORE`` дней до конца триала, период
#: начинается с его конца. Без этого клиника с деньгами на балансе уходила бы
#: в режим чтения между концом триала и ночным запуском.
_RENEWABLE = (
    SubscriptionStatus.TRIAL,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.PAST_DUE,
    SubscriptionStatus.TRIAL_EXPIRED,
    SubscriptionStatus.AWAITING_CARD,
)


@dataclass(slots=True)
class AutopayReport:
    renewed: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)


class AutopaySubscriptionsUseCase:
    def __init__(
        self,
        session: AsyncSession,
        ledger: LedgerService,
        manage: ManageSubscriptionUseCase,
        notifier: Notifier | None = None,
    ) -> None:
        self._session = session
        self._ledger = ledger
        self._manage = manage
        self._notifier = notifier

    async def execute(self, now: datetime | None = None) -> AutopayReport:
        now = now or datetime.now(UTC)
        report = AutopayReport()
        rows = (
            await self._session.execute(
                select(Subscription, ClinicWallet, Clinic)
                .outerjoin(ClinicWallet, ClinicWallet.clinic_id == Subscription.clinic_id)
                .join(Clinic, Clinic.id == Subscription.clinic_id)
                .where(
                    Subscription.status.in_(_RENEWABLE),
                    # Триал — только с деньгами на балансе: без них списывать
                    # нечего, а о конце триала предупреждает своё задание.
                    or_(
                        Subscription.status != SubscriptionStatus.TRIAL,
                        ClinicWallet.balance_tiyin > 0,
                    ),
                )
            )
        ).all()
        for subscription, found, clinic in rows:
            on_trial = subscription.status is SubscriptionStatus.TRIAL
            # Кошелька ещё нет — клиника ничего не платила: заводим, автопродление
            # по умолчанию включено, баланс 0 → предупреждение.
            wallet = found if found is not None else await self._ledger.wallet(clinic.id)
            if not wallet.autopay_enabled:
                continue
            # Отсев по снимку начала прогона; окончательное решение — под
            # блокировкой кошелька в try_autorenew.
            if not subscription_due(subscription, now):
                continue
            try:
                quote = await SubscriptionPricing(self._session).quote(clinic.id, 1)
            except AppError as exc:
                logger.warning("autopay_skipped", clinic_id=str(clinic.id), error=exc.code)
                continue
            if wallet.balance_tiyin >= quote.monthly_tiyin:
                # Пока шёл прогон, колбэк оплаты мог уже открыть период: под
                # блокировкой это видно, и второго списания за тот же месяц нет.
                if await self._ledger.try_autorenew(
                    clinic.id, manage=self._manage, reason="autopay_balance"
                ):
                    report.renewed.append(clinic.name)
                continue
            if on_trial:
                # Денег мало, но это ещё триал: о его конце предупреждает
                # warn_trial_ending — второе сообщение о балансе было бы шумом.
                continue
            # Будущее: здесь попытка списать недостающее с карты по recurring_profile_id.
            period_end = subscription.current_period_end or now
            if (
                wallet.autopay_warned_until is not None
                and wallet.autopay_warned_until >= period_end
            ):
                continue  # об этом периоде уже предупреждали
            wallet.autopay_warned_until = period_end
            await self._session.commit()
            await self._warn(clinic, quote.monthly_tiyin - wallet.balance_tiyin, period_end, now)
            report.warned.append(clinic.name)
        return report

    async def _warn(
        self, clinic: Clinic, shortfall_tiyin: int, period_end: datetime, now: datetime
    ) -> None:
        if self._notifier is None:
            return
        days_left = max(0, (period_end - now).days)
        phone = await self._owner_phone(clinic.id)
        if phone:
            await self._notifier.notify_phone(
                phone,
                balance_low_text(clinic.name, shortfall_tiyin, days_left, pay_url=pay_url()),
                event="balance_low",
            )
        await self._notifier.notify_team(
            team_balance_low_text(clinic.name, shortfall_tiyin, days_left), event="balance_low"
        )

    async def _owner_phone(self, clinic_id: uuid.UUID) -> str | None:
        return (
            await self._session.execute(
                select(Account.phone)
                .join(Membership, Membership.account_id == Account.id)
                .join(Role, Role.id == Membership.role_id)
                .where(
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                    Role.is_owner.is_(True),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
