"""Сборка зависимостей модуля справочников (repository → service → use-case)."""

from __future__ import annotations

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.redis import get_redis
from app.modules.catalog.application.services.catalog_service import CatalogService
from app.modules.catalog.application.use_cases.list_cities import ListCitiesUseCase
from app.modules.catalog.application.use_cases.list_specializations import (
    ListSpecializationsUseCase,
)
from app.modules.catalog.infrastructure.repositories.catalog_repository import CatalogRepository


def get_catalog_repository(
    session: AsyncSession = Depends(get_session),
) -> CatalogRepository:
    return CatalogRepository(session)


def get_catalog_service(
    repository: CatalogRepository = Depends(get_catalog_repository),
    redis: Redis = Depends(get_redis),
) -> CatalogService:
    return CatalogService(repository, redis)


def get_list_cities_use_case(
    service: CatalogService = Depends(get_catalog_service),
) -> ListCitiesUseCase:
    return ListCitiesUseCase(service)


def get_list_specializations_use_case(
    service: CatalogService = Depends(get_catalog_service),
) -> ListSpecializationsUseCase:
    return ListSpecializationsUseCase(service)
