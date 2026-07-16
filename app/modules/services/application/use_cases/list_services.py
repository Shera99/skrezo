"""Use-case: список услуг клиники."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.modules.services.infrastructure.repositories.service_repository import ServiceRepository
from app.shared.db.models.service import Service


class ListServicesUseCase:
    def __init__(self, services: ServiceRepository) -> None:
        self._services = services

    async def execute(self, clinic_id: uuid.UUID, *, active_only: bool) -> Sequence[Service]:
        return await self._services.list(clinic_id, active_only=active_only)
