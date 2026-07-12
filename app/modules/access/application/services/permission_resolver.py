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


class PermissionResolver:
    def __init__(self, role_repository: RoleRepository, redis: Redis) -> None:
        self._roles = role_repository
        self._redis = redis

    async def resolve(self, role_id: uuid.UUID) -> ResolvedRole | None:
        key = _CACHE_KEY.format(role_id)
        cached = await self._redis.get(key)
        if cached is not None:
            data = orjson.loads(cached)
            return ResolvedRole(bool(data["is_owner"]), frozenset(data["permissions"]))

        result = await self._roles.get_permission_keys(role_id)
        if result is None:
            return None
        is_owner, permissions = result
        await self._redis.set(
            key,
            orjson.dumps({"is_owner": is_owner, "permissions": permissions}),
            ex=_CACHE_TTL_SECONDS,
        )
        return ResolvedRole(is_owner, frozenset(permissions))

    async def invalidate(self, role_id: uuid.UUID) -> None:
        """Сбросить кеш прав роли (после изменения прав в UI)."""
        await self._redis.delete(_CACHE_KEY.format(role_id))
