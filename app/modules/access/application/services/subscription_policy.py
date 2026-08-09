"""Что клинике доступно с учётом её подписки: триал-функции и режим чтения.

Два ограничения на одном состоянии подписки, поэтому и политика одна:

* **триал-функции** — на 30-дневном триале доступны только права с
  ``permission.in_trial`` (набор настраивается из админки);
* **режим «только чтение»** — у закрытой подписки (истёкший триал, ожидание
  карты, просрочка, блок, уход клиента) отбираются мутирующие права.

Истечение триала считается по ``trial_ends_at``, а не по статусу в БД:
переводом статуса занимается джоба, и завязка на неё означала бы, что упавший
воркер раздаёт бесплатный доступ. Джоба нужна для честных данных в админке и
отчётах, а не для того, чтобы закрывать доступ.

Состояние подписки кешируется коротко: переход «оплатили» должен отражаться
почти сразу, а «оплаченная клиника случайно в режиме чтения» — жить секунды.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

import orjson
from redis.asyncio import Redis

from app.modules.access.domain.permissions import WRITE_PERMISSION_KEYS
from app.modules.access.infrastructure.repositories.subscription_policy_repository import (
    SubscriptionPolicyRepository,
    SubscriptionState,
)
from app.modules.subscriptions.domain.access import ClinicAccess, clinic_access
from app.shared.db.enums import SubscriptionStatus

_KEYS_CACHE = "access:trial_keys"
#: Ключ кеша состояния подписки клиники (нужен тестам и админ-инвалидации).
CLINIC_STATE_CACHE = "access:clinic_subscription:{}"
_KEYS_TTL = 300
_CLINIC_TTL = 30


class DenyReason(enum.StrEnum):
    """Почему право не сработало, хотя у роли оно есть."""

    TRIAL_RESTRICTED = "trial_restricted"
    SUBSCRIPTION_INACTIVE = "subscription_inactive"


DENY_MESSAGES: dict[DenyReason, str] = {
    DenyReason.TRIAL_RESTRICTED: "Функция недоступна на триале",
    DenyReason.SUBSCRIPTION_INACTIVE: (
        "Подписка неактивна: доступно только чтение — продлите доступ"
    ),
}


class SubscriptionPolicy:
    def __init__(self, repository: SubscriptionPolicyRepository, redis: Redis) -> None:
        self._repo = repository
        self._redis = redis

    async def trial_keys(self) -> frozenset[str]:
        """Множество ключей прав, доступных на триале (кеш)."""
        cached = await self._redis.get(_KEYS_CACHE)
        if cached is not None:
            keys: list[str] = orjson.loads(cached)
            return frozenset(keys)
        keys = await self._repo.trial_keys()
        await self._redis.set(_KEYS_CACHE, orjson.dumps(keys), ex=_KEYS_TTL)
        return frozenset(keys)

    async def state(self, clinic_id: uuid.UUID) -> SubscriptionState | None:
        """Статус подписки и её сроки (кеш с коротким TTL)."""
        key = CLINIC_STATE_CACHE.format(clinic_id)
        cached: str | None = await self._redis.get(key)
        if cached is not None:
            data = orjson.loads(cached)
            if data is None:
                return None
            # ``get`` вместо ключа: записи, закешированные предыдущей версией,
            # не знают про конец периода. TTL короткий, так что расхождение
            # живёт секунды — но падать на нём в момент деплоя незачем.
            period_end = data.get("current_period_end")
            return SubscriptionState(
                status=SubscriptionStatus(data["status"]),
                trial_ends_at=datetime.fromisoformat(data["trial_ends_at"]),
                current_period_end=(
                    datetime.fromisoformat(period_end) if period_end else None
                ),
            )

        state = await self._repo.subscription_state(clinic_id)
        payload = (
            None
            if state is None
            else {
                "status": state.status.value,
                "trial_ends_at": state.trial_ends_at.isoformat(),
                "current_period_end": (
                    state.current_period_end.isoformat()
                    if state.current_period_end is not None
                    else None
                ),
            }
        )
        await self._redis.set(key, orjson.dumps(payload), ex=_CLINIC_TTL)
        return state

    async def invalidate_clinic(self, clinic_id: uuid.UUID) -> None:
        """Сбросить кеш состояния подписки клиники.

        Зовётся после смены статуса из админки: без этого оплативший клиент ещё
        полминуты видит режим чтения и звонит в поддержку.
        """
        await self._redis.delete(CLINIC_STATE_CACHE.format(clinic_id))

    async def access(self, clinic_id: uuid.UUID | None) -> ClinicAccess:
        """Полный доступ или только чтение. Без подписки — полный (соло до провижининга)."""
        if clinic_id is None:
            return ClinicAccess.FULL
        state = await self.state(clinic_id)
        if state is None:
            return ClinicAccess.FULL
        return clinic_access(
            state.status,
            datetime.now(UTC),
            trial_ends_at=state.trial_ends_at,
            current_period_end=state.current_period_end,
        )

    async def deny_reason(
        self, clinic_id: uuid.UUID | None, permission_key: str
    ) -> DenyReason | None:
        """Почему право не действует прямо сейчас (или ``None``, если действует).

        Проверок две, и порядок важен: закрытая подписка сильнее триала — иначе
        клиника с истёкшим триалом получала бы «функция недоступна на триале»,
        хотя дело в сроке, а не в наборе функций.
        """
        if clinic_id is None:
            return None
        state = await self.state(clinic_id)
        if state is None:
            return None

        access = clinic_access(
            state.status,
            datetime.now(UTC),
            trial_ends_at=state.trial_ends_at,
            current_period_end=state.current_period_end,
        )
        if access is ClinicAccess.READ_ONLY:
            return (
                DenyReason.SUBSCRIPTION_INACTIVE
                if permission_key in WRITE_PERMISSION_KEYS
                else None
            )
        if state.status is SubscriptionStatus.TRIAL and permission_key not in (
            await self.trial_keys()
        ):
            return DenyReason.TRIAL_RESTRICTED
        return None

    async def permits(self, clinic_id: uuid.UUID | None, permission_key: str) -> bool:
        """Действует ли право сейчас (краткая форма :meth:`deny_reason`)."""
        return await self.deny_reason(clinic_id, permission_key) is None

    async def filter_permitted(
        self, clinic_id: uuid.UUID | None, permissions: list[str]
    ) -> list[str]:
        """Оставить только действующие права — для ``/auth/me``.

        Фронт рисует кнопки по этому списку, поэтому у клиники в режиме чтения
        мутирующие права из него уходят: иначе интерфейс предлагает действия,
        которые упрутся в 403.
        """
        if clinic_id is None:
            return permissions
        state = await self.state(clinic_id)
        if state is None:
            return permissions

        access = clinic_access(
            state.status,
            datetime.now(UTC),
            trial_ends_at=state.trial_ends_at,
            current_period_end=state.current_period_end,
        )
        if access is ClinicAccess.READ_ONLY:
            return [p for p in permissions if p not in WRITE_PERMISSION_KEYS]
        if state.status is SubscriptionStatus.TRIAL:
            trial = await self.trial_keys()
            return [p for p in permissions if p in trial]
        return permissions

    async def invalidate_keys(self) -> None:
        """Сбросить кеш набора триал-прав (после правки из админки)."""
        await self._redis.delete(_KEYS_CACHE)
