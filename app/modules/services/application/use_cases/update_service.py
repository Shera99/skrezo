"""Use-case: правка услуги клиники (в т.ч. деактивация)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.services.infrastructure.repositories.service_repository import ServiceRepository
from app.shared.db.models.service import Service


class UpdateServiceUseCase:
    def __init__(self, session: AsyncSession, services: ServiceRepository) -> None:
        self._session = session
        self._services = services

    async def execute(
        self, clinic_id: uuid.UUID, service_id: uuid.UUID, fields: dict[str, Any]
    ) -> Service:
        service = await self._services.get(clinic_id, service_id)
        if service is None:
            raise NotFoundError("Услуга не найдена", code="service_not_found")
        await self._services.update(service, fields)
        await self._session.commit()
        return service
