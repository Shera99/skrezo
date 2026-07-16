"""Use-case: создать услугу клиники."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.services.infrastructure.repositories.service_repository import ServiceRepository
from app.shared.db.models.service import Service


class CreateServiceUseCase:
    def __init__(self, session: AsyncSession, services: ServiceRepository) -> None:
        self._session = session
        self._services = services

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        name: str,
        description: str | None,
        price: int,
        duration: int | None,
    ) -> Service:
        service = await self._services.create(
            clinic_id, name=name, description=description, price=price, duration=duration
        )
        await self._session.commit()
        return service
