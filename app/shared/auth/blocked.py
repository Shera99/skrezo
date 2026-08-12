"""Проверка блокировки аккаунта на каждом запросе.

Блокировка обязана действовать сразу, иначе она бессмысленна: отбирают доступ
именно тогда, когда человек не должен успеть ничего сделать. Отзыва сессий для
этого мало — уже выданный access-токен подписан и живёт свои 15 минут, а
проверять его нечем, кроме обращения к состоянию аккаунта.

Кеш в Redis по той же причине, что у прав роли: проверка идёт на каждый
авторизованный запрос — и клиничный, и админский. Блокировка и разблокировка
сбрасывают кеш, поэтому TTL здесь только страховка от пропущенного сброса.
"""

from __future__ import annotations

import uuid

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.redis import get_redis
from app.shared.db.models.account import Account

_CACHE_TTL_SECONDS = 300
_CACHE_KEY = "auth:blocked:{}"
_BLOCKED = "1"
_ALLOWED = "0"


class AccountBlockResolver:
    def __init__(self, session: AsyncSession, redis: Redis) -> None:
        self._session = session
        self._redis = redis

    async def is_blocked(self, account_id: uuid.UUID) -> bool:
        key = _CACHE_KEY.format(account_id)
        cached: str | None = await self._redis.get(key)
        if cached is not None:
            return cached == _BLOCKED

        blocked_at = (
            await self._session.execute(
                select(Account.blocked_at).where(Account.id == account_id)
            )
        ).scalar_one_or_none()
        blocked = blocked_at is not None
        await self._redis.set(
            key, _BLOCKED if blocked else _ALLOWED, ex=_CACHE_TTL_SECONDS
        )
        return blocked

    async def invalidate(self, account_id: uuid.UUID) -> None:
        """Сбросить кеш (после блокировки или разблокировки)."""
        await self._redis.delete(_CACHE_KEY.format(account_id))


def get_account_block_resolver(
    session: AsyncSession = Depends(get_session),
    redis: Redis = Depends(get_redis),
) -> AccountBlockResolver:
    return AccountBlockResolver(session, redis)
