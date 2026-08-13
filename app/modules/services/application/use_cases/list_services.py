"""Use-case: список услуг клиники."""

from __future__ import annotations

import uuid

from app.modules.services.infrastructure.repositories.service_repository import ServiceRepository
from app.shared.db.models.service import Service


class ListServicesUseCase:
    def __init__(self, services: ServiceRepository) -> None:
        self._services = services

    async def execute(
        self, clinic_id: uuid.UUID, *, active_only: bool
    ) -> list[tuple[Service, list[uuid.UUID] | None]]:
        """Услуги клиники, каждая со своим набором врачей.

        Врачи всех услуг берутся одним запросом: прайс читают на каждом открытии
        записи, и запрос на услугу превратил бы список в N+1.
        """
        services = await self._services.list(clinic_id, active_only=active_only)
        doctors = await self._services.doctors_by_service([s.id for s in services])
        return [(s, doctors.get(s.id)) for s in services]
