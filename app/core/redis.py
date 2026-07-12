"""Подключение к Redis.

Используется под кеш справочников/дашборда, хранилище OTP-кодов,
rate-limit, refresh-токены и брокер фоновых задач (arq).
"""

from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.core.config import settings

# Единый пул подключений на процесс. decode_responses=True — работаем со
# строками, а не с байтами (JSON-значения кодируем/декодируем сами).
redis_client: Redis = from_url(  # type: ignore[no-untyped-call]
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)


async def get_redis() -> Redis:
    """FastAPI-зависимость: клиент Redis (общий пул)."""
    return redis_client
