"""Use-case: предупредить, что оплаченный период скоро кончится.

Зеркало :mod:`warn_trial_ending`, и нужен по той же причине, только острее: у
триала конец ожидаем, а вот платящий клиент про свою дату не помнит и узнаёт о
ней в тот момент, когда касса перестала принимать оплату.

Идемпотентность — на отметке ``period_warning_sent_at``, которую ставит тот же
UPDATE, что отбирает клиники. Отметка **сбрасывается при каждой активации** (см.
``SubscriptionRepository.activate``): период повторяется, и предупреждение
должно приходить перед каждым концом, а не однажды за всю жизнь клиента.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.subscriptions.domain.messages import period_ending_text, team_period_ending_text
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.cabinet_links import pay_url
from app.shared.notifications.notifier import Notifier


class WarnPeriodEndingUseCase:
    def __init__(
        self,
        session: AsyncSession,
        subscriptions: SubscriptionRepository,
        notifier: Notifier | None = None,
    ) -> None:
        self._session = session
        self._subscriptions = subscriptions
        self._notifier = notifier

    async def execute(self, days_before: int, now: datetime | None = None) -> int:
        """Предупредить всех, у кого оплата кончается в ближайшие ``days_before`` дней."""
        moment = now or datetime.now(UTC)
        claimed = await self._subscriptions.claim_period_warnings(
            moment, moment + timedelta(days=days_before)
        )
        await self._session.commit()

        if self._notifier is None or not claimed:
            return len(claimed)

        for subscription in claimed:
            # Округляем вверх, как и в предупреждении о триале: «остался 1 день»
            # честнее, чем «0 дней», когда до конца ещё несколько часов.
            days_left = max(1, -((moment - subscription.deadline).days))
            if subscription.owner_phone is not None:
                await self._notifier.notify_phone(
                    subscription.owner_phone,
                    period_ending_text(subscription.clinic_name, days_left, pay_url=pay_url()),
                    event="period_ending",
                )
            await self._notifier.notify_team(
                team_period_ending_text(subscription.clinic_name, days_left),
                event="period_ending",
            )
        return len(claimed)
