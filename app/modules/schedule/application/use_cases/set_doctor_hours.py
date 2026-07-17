"""Use-case: заменить индивидуальный график врача (все 7 дней разом)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.schedule.domain.models import DoctorHours
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


class SetDoctorHoursUseCase:
    def __init__(self, session: AsyncSession, schedule: ScheduleRepository) -> None:
        self._session = session
        self._schedule = schedule

    async def execute(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID, rows: Sequence[DoctorHours]
    ) -> list[DoctorHours]:
        # Членство обязано быть из этой клиники — иначе правка чужого графика.
        if not await self._schedule.is_doctor_in_clinic(clinic_id, membership_id):
            raise NotFoundError("Врач не найден в этой клинике", code="doctor_not_found")
        _validate(rows)
        await self._schedule.replace_doctor_hours(clinic_id, membership_id, rows)
        await self._session.commit()
        return sorted(rows, key=lambda r: r.weekday)


def _validate(rows: Sequence[DoctorHours]) -> None:
    """Ровно 7 дней без повторов; начало раньше конца."""
    weekdays = sorted(r.weekday for r in rows)
    if weekdays != list(range(7)):
        raise ValidationAppError(
            "Нужно передать ровно 7 дней недели (0=пн … 6=вс) без повторов",
            code="invalid_weekdays",
        )
    for row in rows:
        if row.is_working and row.starts_at >= row.ends_at:
            raise ValidationAppError(
                f"День {row.weekday}: начало смены должно быть раньше конца",
                code="invalid_doctor_hours",
            )
