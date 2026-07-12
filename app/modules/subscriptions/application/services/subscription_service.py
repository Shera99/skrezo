"""Прикладной сервис подписок: создание триала при провижининге.

``trial_started_at = now``, ``trial_ends_at = +30 дней``,
``card_attach_deadline = +7 дней`` (из настроек). Пишет запись подписки и
стартовое событие статуса.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.subscription import Subscription


class SubscriptionService:
    def __init__(self, repository: SubscriptionRepository) -> None:
        self._repo = repository

    async def create_trial(self, clinic_id: uuid.UUID) -> Subscription:
        # 1 месяц бесплатно всем, полный доступ, без карты — card_attach_deadline не ставим.
        now = datetime.now(UTC)
        subscription = await self._repo.create(
            clinic_id=clinic_id,
            status=SubscriptionStatus.TRIAL,
            trial_started_at=now,
            trial_ends_at=now + timedelta(days=settings.trial_days),
        )
        await self._repo.add_event(
            subscription_id=subscription.id,
            from_status=None,
            to_status=SubscriptionStatus.TRIAL,
            reason="provisioned",
        )
        return subscription
