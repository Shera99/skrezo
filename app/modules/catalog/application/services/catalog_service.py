"""Прикладной сервис справочников: кеш в Redis поверх репозитория.

Справочники меняются только миграцией/сидом, поэтому кешируются по языку.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import orjson
from redis.asyncio import Redis

from app.modules.catalog.infrastructure.repositories.catalog_repository import CatalogRepository
from app.shared.db.enums import Lang

_CACHE_TTL_SECONDS = 3600


class CatalogService:
    def __init__(self, repository: CatalogRepository, redis: Redis) -> None:
        self._repo = repository
        self._redis = redis

    async def list_cities(self, lang: Lang) -> list[dict[str, Any]]:
        return await self._cached("catalog:cities", lang, self._repo.list_cities)

    async def list_specializations(self, lang: Lang) -> list[dict[str, Any]]:
        return await self._cached("catalog:specializations", lang, self._repo.list_specializations)

    async def _cached(
        self,
        prefix: str,
        lang: Lang,
        loader: Callable[[Lang], Awaitable[list[dict[str, Any]]]],
    ) -> list[dict[str, Any]]:
        cache_key = f"{prefix}:{lang.value}"
        cached = await self._redis.get(cache_key)
        if cached is not None:
            data: list[dict[str, Any]] = orjson.loads(cached)
            return data
        data = await loader(lang)
        await self._redis.set(cache_key, orjson.dumps(data), ex=_CACHE_TTL_SECONDS)
        return data
