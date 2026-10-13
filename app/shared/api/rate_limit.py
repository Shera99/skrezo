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


async def enforce_rate_limit(
    redis: Redis,
    key: str,
    limit: int,
    window: int,
    *,
    code: str | None = None,
    message: str = "Слишком много попыток, попробуйте позже",
) -> None:
    """Учесть попытку по ключу; бросить 429 при превышении лимита в окне.

    Срок до сброса окна — и в теле (``retryAfter``), и в заголовке
    ``Retry-After``: клиент показывает точное «через N секунд», а прокси и
    HTTP-библиотеки понимают заголовок сами. ``code`` — свой машинный код
    вместо общего ``too_many_requests`` (например, лимит ассистента).
    """
    count = await incr_window(redis, key, window)
    if count > limit:
        ttl = await redis.ttl(key)
        raise too_many_requests(ttl if ttl and ttl > 0 else window, code=code, message=message)


async def incr_window(redis: Redis, key: str, window: int) -> int:
    """Прибавить попытку в фиксированном окне; вернуть счётчик.

    Окно заводится и увеличивается одной транзакцией (``SET NX EX`` + ``INCR``):
    раньше ``INCR`` и ``EXPIRE`` шли двумя командами, и сбой между ними
    оставлял ключ без срока — лимит переставал сбрасываться навсегда.
    """
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(key, 0, ex=window, nx=True)
        pipe.incr(key)
        _, count = await pipe.execute()
    return int(count)


def too_many_requests(
    retry_after: int, *, code: str | None = None, message: str = "Слишком много запросов"
) -> TooManyRequestsError:
    """429 со сроком повтора — и в теле (``retryAfter``), и в заголовке ``Retry-After``."""
    return TooManyRequestsError(
        message,
        code=code,
        extra={"retryAfter": retry_after},
        headers={"Retry-After": str(retry_after)},
    )


def ip_rate_limit(*, scope: str, limit: int, window: int) -> Callable[..., Awaitable[None]]:
    """Зависимость: лимит по IP клиента для указанного ``scope``."""

    async def _dep(request: Request, redis: Redis = Depends(get_redis)) -> None:
        await enforce_rate_limit(redis, f"rl:{scope}:{client_ip(request)}", limit, window)

    return _dep
