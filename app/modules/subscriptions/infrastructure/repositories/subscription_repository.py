"""Репозиторий подписок (запись; коммит — на стороне транзакции провижининга)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.subscription import Subscription
from app.shared.db.models.subscription_event import SubscriptionEvent


class SubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_clinic(self, clinic_id: uuid.UUID) -> Subscription | None:
        """Подписка клиники (одна активная запись на клинику)."""
        return (
            await self._session.execute(
                select(Subscription).where(Subscription.clinic_id == clinic_id)
            )
        ).scalar_one_or_none()

    async def create(
        self,
        *,
        clinic_id: uuid.UUID,
        status: SubscriptionStatus,
        trial_started_at: datetime,
        trial_ends_at: datetime,
        card_attach_deadline: datetime | None = None,
    ) -> Subscription:
        subscription = Subscription(
            clinic_id=clinic_id,
            status=status,
            trial_started_at=trial_started_at,
            trial_ends_at=trial_ends_at,
            card_attach_deadline=card_attach_deadline,
        )
        self._session.add(subscription)
        await self._session.flush()
        return subscription

    async def expire_trials(self, now: datetime) -> list[uuid.UUID]:
        """Перевести триалы с истёкшим сроком в ``trial_expired``.

        Один UPDATE с ``RETURNING`` вместо «выбрать и обновить по одной»: клиник
        может быть много, а условие ``status = 'trial'`` делает операцию
        идемпотентной — повторный прогон ничего не найдёт.
        """
        rows = (
            await self._session.execute(
                update(Subscription)
                .where(
                    Subscription.status == SubscriptionStatus.TRIAL,
                    Subscription.trial_ends_at <= now,
                )
                .values(status=SubscriptionStatus.TRIAL_EXPIRED)
                .returning(Subscription.id)
            )
        ).scalars()
        return list(rows)

    async def add_event(
        self,
        *,
        subscription_id: uuid.UUID,
        from_status: SubscriptionStatus | None,
        to_status: SubscriptionStatus,
        reason: str | None = None,
    ) -> None:
        self._session.add(
            SubscriptionEvent(
                subscription_id=subscription_id,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
            )
        )
        await self._session.flush()
