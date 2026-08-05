"""Резолвер прав роли с кешем в Redis.

По ``role_id`` отдаёт эффективные права (владелец → все). Кешируется, т.к.
проверяется на каждый защищённый запрос; при правке прав роли — инвалидировать.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import orjson
from redis.asyncio import Redis

from app.modules.access.infrastructure.repositories.role_repository import RoleRepository

_CACHE_TTL_SECONDS = 300
_CACHE_KEY = "access:role_perms:{}"


@dataclass(frozen=True, slots=True)
class ResolvedRole:
    is_owner: bool
    permissions: frozenset[str]
    #: Потолок скидки роли в процентах (сырое значение из БД).
    max_discount_percent: int = 0

    @property
    def discount_ceiling(self) -> int:
        """Насколько эта роль вправе скинуть.

        У владельца всегда 100: его роль не редактируется, и полный доступ —
        такой же инвариант, как и полный набор прав. Заодно это страхует от
        старого кеша без потолка после выката.
        """
        return 100 if self.is_owner else self.max_discount_percent


class PermissionResolver:
    def __init__(self, role_repository: RoleRepository, redis: Redis) -> None:
        self._roles = role_repository
        self._redis = redis

    async def resolve(self, role_id: uuid.UUID) -> ResolvedRole | None:
        key = _CACHE_KEY.format(role_id)
        cached = await self._redis.get(key)
        if cached is not None:
            data = orjson.loads(cached)
            # get: записи, положенные в кеш до появления потолка, ещё живы.
            return ResolvedRole(
                bool(data["is_owner"]),
                frozenset(data["permissions"]),
                int(data.get("max_discount_percent", 0)),
            )

        result = await self._roles.get_permission_keys(role_id)
        if result is None:
            return None
        is_owner, permissions, max_discount = result
        await self._redis.set(
            key,
            orjson.dumps(
                {
                    "is_owner": is_owner,
                    "permissions": permissions,
                    "max_discount_percent": max_discount,
                }
            ),
            ex=_CACHE_TTL_SECONDS,
        )
        return ResolvedRole(is_owner, frozenset(permissions), max_discount)

    async def invalidate(self, role_id: uuid.UUID) -> None:
        """Сбросить кеш прав роли (после изменения прав в UI)."""
        await self._redis.delete(_CACHE_KEY.format(role_id))
