"""Use-case: предупредить владельца, что пробный период скоро кончится.

Без предупреждения клиника узнаёт о конце триала в тот момент, когда касса
перестаёт принимать оплату, — это худшее место для новости. Заодно тот же факт
уходит в чат команды: у менеджера появляется повод позвонить, пока клиент ещё
работает.

Идемпотентность обеспечивает не эта функция, а сам запрос: отметка
``trial_warning_sent_at`` ставится тем же UPDATE, что отбирает клиники. Джоба
может запуститься дважды — второй раз она просто ничего не найдёт.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.subscriptions.domain.messages import team_trial_ending_text, trial_ending_text
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.cabinet_links import pay_url
from app.shared.notifications.notifier import Notifier


class WarnTrialEndingUseCase:
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
        """Предупредить всех, у кого триал кончается в ближайшие ``days_before`` дней.

        Возвращает число предупреждённых клиник. Клиника без активного владельца
        считается предупреждённой (отметка уже стоит) — писать ей просто некому,
        и держать её в выборке значило бы каждый день перебирать одно и то же.
        """
        moment = now or datetime.now(UTC)
        claimed = await self._subscriptions.claim_trial_warnings(
            moment, moment + timedelta(days=days_before)
        )
        await self._session.commit()

        if self._notifier is None or not claimed:
            return len(claimed)

        for subscription in claimed:
            # Округляем вверх: «остался 1 день» честнее, чем «0 дней», когда до
            # конца ещё несколько часов.
            days_left = max(1, -((moment - subscription.deadline).days))
            if subscription.owner_phone is not None:
                await self._notifier.notify_phone(
                    subscription.owner_phone,
                    trial_ending_text(subscription.clinic_name, days_left, pay_url=pay_url()),
                    event="trial_ending",
                )
            await self._notifier.notify_team(
                team_trial_ending_text(subscription.clinic_name, days_left),
                event="trial_ending",
            )
        return len(claimed)
