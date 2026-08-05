"""Use-case: подписка текущей клиники (статус, срок, сколько дней осталось)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.errors import NotFoundError
from app.modules.subscriptions.domain.status import SubscriptionView, build_view
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)


class GetSubscriptionUseCase:
    def __init__(self, subscriptions: SubscriptionRepository) -> None:
        self._subscriptions = subscriptions

    async def execute(self, clinic_id: uuid.UUID) -> SubscriptionView:
        """Вернуть подписку клиники с посчитанным сроком действия.

        Подписка заводится вместе с клиникой (провижининг), поэтому её отсутствие
        — не штатная ситуация, а битые данные: отдаём 404, а не пустой ответ,
        который клиент показал бы как «бессрочно».
        """
        subscription = await self._subscriptions.by_clinic(clinic_id)
        if subscription is None:
            raise NotFoundError("Подписка клиники не найдена", code="subscription_not_found")
        return build_view(subscription, datetime.now(UTC))
