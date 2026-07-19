"""Use-case: список отсутствий врача (все записи, в таймзоне клиники)."""

from __future__ import annotations

import uuid
from dataclasses import replace

from app.core.errors import NotFoundError
from app.modules.schedule.application.services.availability_service import AvailabilityService
from app.modules.schedule.domain.models import TimeOffList
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


class ListTimeOffUseCase:
    def __init__(self, schedule: ScheduleRepository, availability: AvailabilityService) -> None:
        self._schedule = schedule
        self._availability = availability

    async def execute(self, clinic_id: uuid.UUID, membership_id: uuid.UUID) -> TimeOffList:
        """Все отсутствия врача этой клиники. Интервалы — в таймзоне клиники."""
        if not await self._schedule.is_doctor_in_clinic(clinic_id, membership_id):
            raise NotFoundError("Врач не найден в этой клинике", code="doctor_not_found")

        tz = await self._availability.clinic_timezone(clinic_id)
        # Из БД timestamptz приходит в UTC — нормализуем в таймзону клиники.
        records = [
            replace(r, starts_at=r.starts_at.astimezone(tz), ends_at=r.ends_at.astimezone(tz))
            for r in await self._schedule.list_time_off_records(clinic_id, membership_id)
        ]
        clinic = await self._schedule.get_clinic(clinic_id)
        return TimeOffList(
            timezone=clinic.timezone if clinic is not None else str(tz),
            doctor_membership_id=membership_id,
            records=records,
        )
