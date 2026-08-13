"""Use-case: подписка текущей клиники (статус, срок, места)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.errors import NotFoundError
from app.modules.subscriptions.domain.status import SubscriptionView, build_view
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)


@dataclass(slots=True)
class ClinicSubscriptionResult:
    """Подписка вместе с занятостью мест по тарифу.

    Места живут рядом с подпиской, а не в ``SubscriptionView``: срок действия —
    свойство самой подписки и считается одинаково для клиники и для админки
    Skrezo, а «сколько ещё сотрудников можно добавить» нужно только кабинету.
    """

    view: SubscriptionView
    seats_used: int
    seats_limit: int | None


class GetSubscriptionUseCase:
    def __init__(self, subscriptions: SubscriptionRepository) -> None:
        self._subscriptions = subscriptions

    async def execute(self, clinic_id: uuid.UUID) -> ClinicSubscriptionResult:
        """Вернуть подписку клиники с посчитанным сроком действия и местами.

        Подписка заводится вместе с клиникой (провижининг), поэтому её отсутствие
        — не штатная ситуация, а битые данные: отдаём 404, а не пустой ответ,
        который клиент показал бы как «бессрочно».
        """
        subscription = await self._subscriptions.by_clinic(clinic_id)
        if subscription is None:
            raise NotFoundError("Подписка клиники не найдена", code="subscription_not_found")
        seats_used, seats_limit = await self._subscriptions.seats(clinic_id)
        return ClinicSubscriptionResult(
            view=build_view(subscription, datetime.now(UTC)),
            seats_used=seats_used,
            seats_limit=seats_limit,
        )
