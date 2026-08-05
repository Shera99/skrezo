"""Use-case: свободные слоты врача на день."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.schedule.application.services.availability_service import AvailabilityService
from app.modules.schedule.domain.availability import SLOT_STEP_MIN, slice_slots
from app.modules.schedule.domain.models import DaySlots
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


class GetSlotsUseCase:
    def __init__(self, schedule: ScheduleRepository, availability: AvailabilityService) -> None:
        self._schedule = schedule
        self._availability = availability

    async def execute(
        self,
        clinic_id: uuid.UUID,
        doctor_membership_id: uuid.UUID,
        day: date,
        service_id: uuid.UUID | None,
    ) -> DaySlots:
        # Врач обязан быть из этой клиники — иначе чужое расписание (IDOR). Ответ
        # тот же, что и на несуществующего врача: существование чужих не
        # подтверждаем, и такой же 404 отдают остальные ручки по врачу.
        if not await self._schedule.is_doctor_in_clinic(clinic_id, doctor_membership_id):
            raise NotFoundError("Врач не найден в этой клинике", code="doctor_not_found")
        if service_id is not None:
            exists, _ = await self._schedule.service_duration(clinic_id, service_id)
            if not exists:
                raise ValidationAppError("Услуга не из этой клиники", code="invalid_service")

        tz = await self._availability.clinic_timezone(clinic_id)
        duration_min = await self._availability.resolve_duration(clinic_id, service_id, None)
        free = await self._availability.free_spans(clinic_id, doctor_membership_id, day, tz)
        slots = slice_slots(
            free,
            duration=timedelta(minutes=duration_min),
            step=timedelta(minutes=SLOT_STEP_MIN),
        )
        clinic = await self._schedule.get_clinic(clinic_id)
        return DaySlots(
            day=day,
            timezone=clinic.timezone if clinic is not None else str(tz),
            doctor_membership_id=doctor_membership_id,
            duration_min=duration_min,
            slots=slots,
        )
