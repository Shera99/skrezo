"""Use-case: часы работы клиники (все 7 дней)."""

from __future__ import annotations

import uuid

from app.modules.schedule.domain.availability import default_working_hours
from app.modules.schedule.domain.models import WorkingHours
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository

WEEKDAYS = range(7)


class GetWorkingHoursUseCase:
    def __init__(self, schedule: ScheduleRepository) -> None:
        self._schedule = schedule

    async def execute(self, clinic_id: uuid.UUID) -> list[WorkingHours]:
        """Всегда отдаём ровно 7 дней: ненастроенные — значениями по умолчанию."""
        configured = await self._schedule.working_hours(clinic_id)
        return [configured.get(day) or default_working_hours(day) for day in WEEKDAYS]
