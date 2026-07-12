"""Use-case: список городов на языке запроса."""

from __future__ import annotations

from typing import Any

from app.modules.catalog.application.services.catalog_service import CatalogService
from app.shared.db.enums import Lang


class ListCitiesUseCase:
    def __init__(self, catalog_service: CatalogService) -> None:
        self._catalog = catalog_service

    async def execute(self, lang: Lang) -> list[dict[str, Any]]:
        return await self._catalog.list_cities(lang)
