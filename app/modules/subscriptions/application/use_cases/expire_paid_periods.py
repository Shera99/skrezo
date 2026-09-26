"""Use-case: перевести подписки с истёкшим оплаченным периодом в ``past_due``.

Зеркало :mod:`expire_trials`, и оговорка та же: **доступ закрывает не эта
задача**. Клиника с кончившимся периодом уходит в режим чтения сразу по дате
``current_period_end`` (см. ``subscriptions/domain/access``), даже если воркер
лежит. Задача приводит статус в БД к реальности и сообщает людям — админка,
дашборд и отчёты не должны показывать должника оплаченным клиентом.

Идемпотентность — на условии ``status = 'active'``: повторный прогон никого не
трогает второй раз, поэтому и уведомление уходит однократно.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.subscriptions.domain.messages import (
    paid_period_expired_text,
    team_paid_periods_expired_text,
)
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.cabinet_links import pay_url
from app.shared.db.enums import SubscriptionStatus
from app.shared.notifications.notifier import Notifier


class ExpirePaidPeriodsUseCase:
    def __init__(
        self,
        session: AsyncSession,
        subscriptions: SubscriptionRepository,
        notifier: Notifier | None = None,
    ) -> None:
        self._session = session
        self._subscriptions = subscriptions
        self._notifier = notifier

    async def execute(self, now: datetime | None = None) -> int:
        """Перевести все просроченные подписки; вернуть число переведённых клиник."""
        moment = now or datetime.now(UTC)
        expired = await self._subscriptions.expire_paid_periods(moment)
        for subscription in expired:
            await self._subscriptions.add_event(
                subscription_id=subscription.subscription_id,
                from_status=SubscriptionStatus.ACTIVE,
                to_status=SubscriptionStatus.PAST_DUE,
                reason="period_ended",
            )
        await self._session.commit()

        # Уведомления — только после коммита: не сложился перевод, не должно
        # быть и сообщений о нём.
        if self._notifier is not None and expired:
            for subscription in expired:
                if subscription.owner_phone is None:
                    continue
                await self._notifier.notify_phone(
                    subscription.owner_phone,
                    paid_period_expired_text(subscription.clinic_name, pay_url=pay_url()),
                    event="paid_period_expired",
                )
            await self._notifier.notify_team(
                team_paid_periods_expired_text([s.clinic_name for s in expired]),
                event="paid_period_expired",
            )
        return len(expired)
