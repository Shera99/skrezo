"""Use-case: создать услугу клиники."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.services.infrastructure.repositories.service_repository import ServiceRepository
from app.shared.db.enums import ToothStatus
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
        price_tiyin: int,
        duration: int | None,
        tooth_outcome: ToothStatus | None = None,
    ) -> Service:
        service = await self._services.create(
            clinic_id,
            name=name,
            description=description,
            price_tiyin=price_tiyin,
            duration=duration,
            tooth_outcome=tooth_outcome,
        )
        await self._session.commit()
        return service
