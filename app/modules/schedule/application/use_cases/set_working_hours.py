"""Use-case: заменить часы работы клиники (все 7 дней разом)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationAppError
from app.modules.schedule.domain.models import WorkingHours
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


class SetWorkingHoursUseCase:
    def __init__(self, session: AsyncSession, schedule: ScheduleRepository) -> None:
        self._session = session
        self._schedule = schedule

    async def execute(
        self, clinic_id: uuid.UUID, rows: Sequence[WorkingHours]
    ) -> list[WorkingHours]:
        _validate(rows)
        await self._schedule.replace_working_hours(clinic_id, rows)
        await self._session.commit()
        return sorted(rows, key=lambda r: r.weekday)


def _validate(rows: Sequence[WorkingHours]) -> None:
    """Ровно 7 дней без повторов; обед — внутри рабочих часов."""
    weekdays = sorted(r.weekday for r in rows)
    if weekdays != list(range(7)):
        raise ValidationAppError(
            "Нужно передать ровно 7 дней недели (0=пн … 6=вс) без повторов",
            code="invalid_weekdays",
        )
    for row in rows:
        if not row.is_working:
            continue
        if row.opens_at >= row.closes_at:
            raise ValidationAppError(
                f"День {row.weekday}: открытие должно быть раньше закрытия",
                code="invalid_working_hours",
            )
        if (row.break_start is None) != (row.break_end is None):
            raise ValidationAppError(
                f"День {row.weekday}: обед задаётся обеими границами",
                code="invalid_break",
            )
        if row.break_start is not None and row.break_end is not None:
            if row.break_start >= row.break_end:
                raise ValidationAppError(
                    f"День {row.weekday}: начало обеда должно быть раньше конца",
                    code="invalid_break",
                )
            if row.break_start < row.opens_at or row.break_end > row.closes_at:
                raise ValidationAppError(
                    f"День {row.weekday}: обед должен быть внутри рабочих часов",
                    code="invalid_break",
                )
