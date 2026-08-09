"""Репозиторий политики подписки: набор триал-прав и состояние подписки клиники."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.permission import Permission
from app.shared.db.models.subscription import Subscription


@dataclass(frozen=True, slots=True)
class SubscriptionState:
    """Минимум, по которому решается доступ: статус и оба срока.

    Конец оплаченного периода здесь по той же причине, что и конец триала:
    доступ считается по датам, а не по статусу, который ставит джоба.
    """

    status: SubscriptionStatus
    trial_ends_at: datetime
    #: Конец оплаченного периода; ``None`` — клиника ещё ни разу не платила.
    current_period_end: datetime | None = None


class SubscriptionPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def trial_keys(self) -> list[str]:
        """Ключи прав, доступных на триале (``permission.in_trial = true``)."""
        rows = (
            await self._session.execute(
                select(Permission.key).where(Permission.in_trial.is_(True))
            )
        ).scalars()
        return list(rows)

    async def subscription_state(self, clinic_id: uuid.UUID) -> SubscriptionState | None:
        """Статус подписки и сроки триала/периода; ``None`` — подписки нет."""
        row = (
            await self._session.execute(
                select(
                    Subscription.status,
                    Subscription.trial_ends_at,
                    Subscription.current_period_end,
                ).where(Subscription.clinic_id == clinic_id)
            )
        ).first()
        if row is None:
            return None
        return SubscriptionState(
            status=row[0], trial_ends_at=row[1], current_period_end=row[2]
        )

    async def all_permissions(self) -> list[Permission]:
        """Полный каталог прав (для админ-экрана триал-функций)."""
        rows = (
            await self._session.execute(
                select(Permission).order_by(Permission.sort_order, Permission.key)
            )
        ).scalars()
        return list(rows)

    async def set_in_trial(self, key: str, in_trial: bool) -> bool:
        """Переключить флаг ``in_trial`` у права по ключу. Вернуть, найдено ли право."""
        result = await self._session.execute(
            update(Permission)
            .where(Permission.key == key)
            .values(in_trial=in_trial)
            .returning(Permission.id)
        )
        return result.first() is not None
