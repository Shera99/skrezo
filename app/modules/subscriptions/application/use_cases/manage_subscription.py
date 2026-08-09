"""Use-case: ручное управление подпиской клиники командой Skrezo.

Пока платёжного провайдера нет, оплата приходит переводом на счёт, и отметить её
может только человек. Отсюда две операции: «оплачено до такого-то числа» и
«заблокировать». Обе пишут событие в ``subscription_event`` — по нему потом
видно, кто и когда открыл или закрыл клинике доступ.

⚠️ Обе **сбрасывают кеш состояния подписки**. Без этого оплативший клиент ещё до
полуминуты видит режим чтения и звонит в поддержку — а поддержка видит в базе
``active`` и не понимает вопроса.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.access.application.services.subscription_policy import SubscriptionPolicy
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.subscription import Subscription


class ManageSubscriptionUseCase:
    def __init__(
        self,
        session: AsyncSession,
        subscriptions: SubscriptionRepository,
        policy: SubscriptionPolicy,
    ) -> None:
        self._session = session
        self._subscriptions = subscriptions
        self._policy = policy

    async def activate(
        self,
        clinic_id: uuid.UUID,
        *,
        paid_until: datetime,
        period_start: datetime | None = None,
    ) -> Subscription:
        """Отметить оплату: подписка ``active`` до ``paid_until``.

        Дата конца периода обязательна и должна быть в будущем: подписка,
        оплаченная «до вчера», сразу же уходит в режим чтения — это почти всегда
        опечатка в дате, а не намерение.
        """
        now = datetime.now(UTC)
        if paid_until <= now:
            raise ValidationAppError(
                "Дата окончания периода должна быть в будущем", code="paid_until_in_past"
            )

        subscription = await self._subscriptions.by_clinic(clinic_id)
        if subscription is None:
            raise NotFoundError("Подписка клиники не найдена", code="subscription_not_found")

        from_status = subscription.status
        await self._subscriptions.activate(
            clinic_id, period_start=period_start or now, period_end=paid_until
        )
        await self._subscriptions.add_event(
            subscription_id=subscription.id,
            from_status=from_status,
            to_status=SubscriptionStatus.ACTIVE,
            reason="manual_payment",
        )
        await self._session.commit()
        await self._policy.invalidate_clinic(clinic_id)
        return subscription

    async def block(self, clinic_id: uuid.UUID, reason: str | None = None) -> Subscription:
        """Закрыть клинике запись вручную (``blocked``).

        Отличается от просрочки причиной, а не последствиями: режим чтения тот
        же, но по статусу видно, что это решение Skrezo, а не конец оплаты.
        """
        subscription = await self._subscriptions.by_clinic(clinic_id)
        if subscription is None:
            raise NotFoundError("Подписка клиники не найдена", code="subscription_not_found")

        from_status = subscription.status
        await self._subscriptions.set_status(clinic_id, SubscriptionStatus.BLOCKED)
        await self._subscriptions.add_event(
            subscription_id=subscription.id,
            from_status=from_status,
            to_status=SubscriptionStatus.BLOCKED,
            reason=reason or "manual_block",
        )
        await self._session.commit()
        await self._policy.invalidate_clinic(clinic_id)
        return subscription
