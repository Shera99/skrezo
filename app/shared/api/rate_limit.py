"""Rate-limit на Redis (фиксированное окно) для публичных/чувствительных ручек.

Счётчик ``INCR`` с ``EXPIRE`` на первое попадание в окно; при превышении —
429 ``too_many_requests`` с ``retryAfter``. Ключ включает IP клиента (и, для
входа, нормализованный телефон), чтобы разделять перебор по аккаунтам и адресам.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.core.errors import TooManyRequestsError
from app.core.redis import get_redis


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def enforce_rate_limit(redis: Redis, key: str, limit: int, window: int) -> None:
    """Учесть попытку по ключу; бросить 429 при превышении лимита в окне."""
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window)
    if count > limit:
        ttl = await redis.ttl(key)
        raise TooManyRequestsError(
            "Слишком много попыток, попробуйте позже",
            extra={"retryAfter": ttl if ttl and ttl > 0 else window},
        )


def ip_rate_limit(*, scope: str, limit: int, window: int) -> Callable[..., Awaitable[None]]:
    """Зависимость: лимит по IP клиента для указанного ``scope``."""

    async def _dep(request: Request, redis: Redis = Depends(get_redis)) -> None:
        await enforce_rate_limit(redis, f"rl:{scope}:{client_ip(request)}", limit, window)

    return _dep
