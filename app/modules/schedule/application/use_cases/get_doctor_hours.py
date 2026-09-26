"""Use-case: индивидуальный график врача (все 7 дней)."""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError
from app.modules.schedule.application.use_cases.get_working_hours import WEEKDAYS
from app.modules.schedule.domain.availability import default_working_hours
from app.modules.schedule.domain.models import DoctorHours
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


class GetDoctorHoursUseCase:
    def __init__(self, schedule: ScheduleRepository) -> None:
        self._schedule = schedule

    async def execute(self, clinic_id: uuid.UUID, membership_id: uuid.UUID) -> list[DoctorHours]:
        """Ровно 7 дней. Если график не задан — врач работает по часам клиники."""
        if not await self._schedule.is_doctor_in_clinic(clinic_id, membership_id):
            raise NotFoundError("Врач не найден в этой клинике", code="doctor_not_found")

        configured = await self._schedule.doctor_hours(clinic_id, membership_id)
        clinic_hours = await self._schedule.working_hours(clinic_id)
        result: list[DoctorHours] = []
        for day in WEEKDAYS:
            individual = configured.get(day)
            if individual is not None:
                result.append(individual)
                continue
            fallback = clinic_hours.get(day) or default_working_hours(day)
            result.append(
                DoctorHours(
                    weekday=day,
                    starts_at=fallback.opens_at,
                    ends_at=fallback.closes_at,
                    is_working=fallback.is_working,
                    source="clinic",
                )
            )
        return result
