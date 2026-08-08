"""Use-case: перевести истёкшие триалы в ``trial_expired``.

Доступ клиники закрывается **не этим** — его считает политика подписки по
``trial_ends_at`` (см. ``access/application/services/subscription_policy``).
Джоба материализует факт: статус в БД перестаёт врать, клиника с истёкшим
триалом видна в админке и дашборде, а в ``subscription_event`` остаётся запись
о переходе — по ней потом сверяют, когда именно клиент «отвалился».

Отсюда и запуск по расписанию, и идемпотентность: повторный прогон никого не
трогает второй раз (условие ``status = 'trial'`` перестаёт выполняться).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.db.enums import SubscriptionStatus


class ExpireTrialsUseCase:
    def __init__(self, session: AsyncSession, subscriptions: SubscriptionRepository) -> None:
        self._session = session
        self._subscriptions = subscriptions

    async def execute(self, now: datetime | None = None) -> int:
        """Перевести все истёкшие триалы; вернуть число переведённых клиник."""
        moment = now or datetime.now(UTC)
        expired = await self._subscriptions.expire_trials(moment)
        for subscription_id in expired:
            await self._subscriptions.add_event(
                subscription_id=subscription_id,
                from_status=SubscriptionStatus.TRIAL,
                to_status=SubscriptionStatus.TRIAL_EXPIRED,
                reason="trial_ended",
            )
        await self._session.commit()
        return len(expired)
