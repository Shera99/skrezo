"""Use-case: перевести истёкшие триалы в ``trial_expired`` и сказать об этом.

Доступ клиники закрывается **не этим** — его считает политика подписки по
``trial_ends_at`` (см. ``access/application/services/subscription_policy``).
Джоба материализует факт: статус в БД перестаёт врать, клиника с истёкшим
триалом видна в админке и дашборде, а в ``subscription_event`` остаётся запись
о переходе — по ней потом сверяют, когда именно клиент «отвалился».

Отсюда и запуск по расписанию, и идемпотентность: повторный прогон никого не
трогает второй раз (условие ``status = 'trial'`` перестаёт выполняться). На той
же идемпотентности держится и уведомление — оно уходит только тем, кого перевёл
именно этот прогон, поэтому второй раз ни владельцу, ни команде не напишут.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.clients.application.services.team_card_service import ClientTeamCardService
from app.modules.subscriptions.domain.messages import trial_expired_text
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.cabinet_links import pay_url
from app.shared.db.enums import SubscriptionStatus
from app.shared.notifications.notifier import Notifier


class ExpireTrialsUseCase:
    def __init__(
        self,
        session: AsyncSession,
        subscriptions: SubscriptionRepository,
        notifier: Notifier | None = None,
        team_cards: ClientTeamCardService | None = None,
    ) -> None:
        self._session = session
        self._subscriptions = subscriptions
        self._notifier = notifier
        self._team_cards = team_cards

    async def execute(self, now: datetime | None = None) -> int:
        """Перевести все истёкшие триалы; вернуть число переведённых клиник."""
        moment = now or datetime.now(UTC)
        expired = await self._subscriptions.expire_trials(moment)
        for subscription in expired:
            await self._subscriptions.add_event(
                subscription_id=subscription.subscription_id,
                from_status=SubscriptionStatus.TRIAL,
                to_status=SubscriptionStatus.TRIAL_EXPIRED,
                reason="trial_ended",
            )
        await self._session.commit()

        # Уведомления — только после коммита: не сложился перевод, не должно
        # быть и писем о нём.
        if self._notifier is not None and expired:
            for subscription in expired:
                if subscription.owner_phone is None:
                    continue
                await self._notifier.notify_phone(
                    subscription.owner_phone,
                    trial_expired_text(subscription.clinic_name, pay_url=pay_url()),
                    event="trial_expired",
                )
        # Команде — карточка на каждую клинику: кто это, как связаться и
        # пользовались ли вообще. Повод позвонить и предложить оплату.
        if self._team_cards is not None and expired:
            await self._team_cards.notify_trial_expired([s.clinic_id for s in expired])
        return len(expired)
