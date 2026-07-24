"""Триал-политика: какие права доступны на триале и находится ли клиника на нём.

Оба факта нужны на каждом защищённом запросе (guard прав), поэтому кешируются в
Redis. Набор триал-прав общий на платформу (кеш до правки из админки). Статус
подписки клиники кешируется коротко — переход триал→оплачено отражается почти
сразу, и «оплаченная клиника случайно ограничена триалом» долго не живёт.
"""

from __future__ import annotations

import uuid

import orjson
from redis.asyncio import Redis

from app.modules.access.infrastructure.repositories.trial_policy_repository import (
    TrialPolicyRepository,
)

_KEYS_CACHE = "access:trial_keys"
_CLINIC_CACHE = "access:clinic_trial:{}"
_KEYS_TTL = 300
_CLINIC_TTL = 30


class TrialPolicy:
    def __init__(self, repository: TrialPolicyRepository, redis: Redis) -> None:
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

    async def is_clinic_on_trial(self, clinic_id: uuid.UUID) -> bool:
        """На триале ли клиника (кеш с коротким TTL)."""
        key = _CLINIC_CACHE.format(clinic_id)
        cached: str | None = await self._redis.get(key)
        if cached is not None:
            return cached == "1"
        on_trial = await self._repo.clinic_on_trial(clinic_id)
        await self._redis.set(key, "1" if on_trial else "0", ex=_CLINIC_TTL)
        return on_trial

    async def permits(self, clinic_id: uuid.UUID | None, permission_key: str) -> bool:
        """Разрешено ли право с учётом триала.

        Вне триала (или без клиники) — всегда да; на триале — только если право
        входит в набор триал-функций. Используется всеми guard'ами (общий
        ``require_permission`` и скоуп-guard'ы расписания/кассы/отчётов), чтобы
        триал-ограничение было единым.
        """
        if clinic_id is None or not await self.is_clinic_on_trial(clinic_id):
            return True
        return permission_key in await self.trial_keys()

    async def filter_permitted(
        self, clinic_id: uuid.UUID | None, permissions: list[str]
    ) -> list[str]:
        """Оставить только доступные права, если клиника на триале (для ``/auth/me``)."""
        if clinic_id is None or not await self.is_clinic_on_trial(clinic_id):
            return permissions
        trial = await self.trial_keys()
        return [p for p in permissions if p in trial]

    async def invalidate_keys(self) -> None:
        """Сбросить кеш набора триал-прав (после правки из админки)."""
        await self._redis.delete(_KEYS_CACHE)
