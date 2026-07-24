"""Репозиторий триал-политики: набор триал-прав и статус подписки клиники."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.permission import Permission
from app.shared.db.models.subscription import Subscription


class TrialPolicyRepository:
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

    async def clinic_on_trial(self, clinic_id: uuid.UUID) -> bool:
        """Находится ли подписка клиники в статусе ``trial``."""
        status = (
            await self._session.execute(
                select(Subscription.status).where(Subscription.clinic_id == clinic_id)
            )
        ).scalar_one_or_none()
        return status == SubscriptionStatus.TRIAL

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
