"""Use-case: правка услуги клиники (в т.ч. деактивация)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.services.application.services.doctor_assignment import apply_doctor_ids
from app.modules.services.infrastructure.repositories.service_repository import ServiceRepository
from app.shared.db.models.service import Service


class UpdateServiceUseCase:
    def __init__(self, session: AsyncSession, services: ServiceRepository) -> None:
        self._session = session
        self._services = services

    async def execute(
        self, clinic_id: uuid.UUID, service_id: uuid.UUID, fields: dict[str, Any]
    ) -> tuple[Service, list[uuid.UUID] | None]:
        """Обновить услугу; вернуть её и действующий набор врачей."""
        service = await self._services.get(clinic_id, service_id)
        if service is None:
            raise NotFoundError("Услуга не найдена", code="service_not_found")
        # Врачи живут в своей таблице, а не в колонках услуги: вынимаем их из
        # набора полей до apply_updates, иначе он отвергнет неизвестную колонку.
        doctor_ids = fields.pop("doctor_ids", _UNSET)
        await self._services.update(service, fields)
        if doctor_ids is not _UNSET:
            await apply_doctor_ids(self._services, clinic_id, service_id, doctor_ids)
        await self._session.commit()
        effective = await self._services.doctors_by_service([service_id])
        return service, effective.get(service_id)


#: Отличает «поле не передали» от «передали null» — у doctor_ids это разные
#: намерения: не трогать набор врачей против снять ограничение.
_UNSET = object()
