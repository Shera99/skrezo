"""Резолвер живого членства: работает ли сотрудник в клинике и с какой ролью.

Скоуп клиники приходит в claims токена, но токен выписывается один раз и
переживает увольнение: refresh живёт 30 дней и всё это время обновляет access.
Без проверки на каждом запросе удалённый сотрудник продолжал бы работать —
деактивация членства меняла бы только строку в БД.

Поэтому роль берётся не из токена, а из базы. Побочный выигрыш: смена роли
сотрудника применяется сразу, а не после того, как он перелогинится.

Кеш в Redis — как у прав роли, по той же причине: проверка идёт на каждый
защищённый запрос. Отсутствие членства кешируется тоже (иначе уволенный
сотрудник с работающим клиентом бил бы в базу каждым запросом), поэтому
удаление и смена роли обязаны звать :meth:`invalidate`.
"""

from __future__ import annotations

import uuid

from redis.asyncio import Redis

from app.modules.access.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)

_CACHE_TTL_SECONDS = 300
_CACHE_KEY = "access:membership:{}:{}"
#: Метка «членства нет» — пустую строку Redis не отличить от промаха кеша.
_NO_MEMBERSHIP = "-"


class MembershipResolver:
    def __init__(self, memberships: MembershipRepository, redis: Redis) -> None:
        self._memberships = memberships
        self._redis = redis

    async def resolve(self, account_id: uuid.UUID, clinic_id: uuid.UUID) -> uuid.UUID | None:
        """Вернуть роль активного членства или ``None``, если доступ к клинике закрыт."""
        key = _CACHE_KEY.format(account_id, clinic_id)
        cached: str | None = await self._redis.get(key)
        if cached is not None:
            return None if cached == _NO_MEMBERSHIP else uuid.UUID(cached)

        role_id = await self._memberships.active_role_id(account_id, clinic_id)
        await self._redis.set(
            key,
            _NO_MEMBERSHIP if role_id is None else str(role_id),
            ex=_CACHE_TTL_SECONDS,
        )
        return role_id

    async def invalidate(self, account_id: uuid.UUID, clinic_id: uuid.UUID) -> None:
        """Сбросить кеш членства (после удаления сотрудника или смены его роли)."""
        await self._redis.delete(_CACHE_KEY.format(account_id, clinic_id))
