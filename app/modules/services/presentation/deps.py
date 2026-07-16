"""Зависимости модуля услуг."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.services.application.use_cases.create_service import CreateServiceUseCase
from app.modules.services.application.use_cases.list_services import ListServicesUseCase
from app.modules.services.application.use_cases.update_service import UpdateServiceUseCase
from app.modules.services.infrastructure.repositories.service_repository import ServiceRepository


def get_service_repository(session: AsyncSession = Depends(get_session)) -> ServiceRepository:
    return ServiceRepository(session)


def get_list_services_use_case(
    services: ServiceRepository = Depends(get_service_repository),
) -> ListServicesUseCase:
    return ListServicesUseCase(services)


def get_create_service_use_case(
    session: AsyncSession = Depends(get_session),
    services: ServiceRepository = Depends(get_service_repository),
) -> CreateServiceUseCase:
    return CreateServiceUseCase(session, services)


def get_update_service_use_case(
    session: AsyncSession = Depends(get_session),
    services: ServiceRepository = Depends(get_service_repository),
) -> UpdateServiceUseCase:
    return UpdateServiceUseCase(session, services)
