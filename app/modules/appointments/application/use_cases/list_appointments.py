"""Use-case: список приёмов клиники (фильтры + offset-пагинация)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.errors import ValidationAppError
from app.modules.appointments.domain.models import AppointmentRow
from app.modules.appointments.domain.period import (
    InvalidPeriodBoundError,
    clinic_tz,
    period_end,
    period_start,
)
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.shared.db.enums import AppointmentStatus


@dataclass(slots=True)
class ListAppointmentsResult:
    items: list[AppointmentRow]
    total_count: int
    current_page: int
    page_size: int


class ListAppointmentsUseCase:
    def __init__(self, appointments: AppointmentRepository) -> None:
        self._appointments = appointments

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        patient_id: uuid.UUID | None,
        status: AppointmentStatus | None,
        date_from: str | None,
        date_to: str | None,
        page: int,
        page_size: int,
    ) -> ListAppointmentsResult:
        """Приёмы клиники с фильтрами.

        ``date_from``/``date_to`` — дата (день целиком в поясе клиники) или
        дата-время ISO (без пояса — пояс клиники); ``date_to`` включительно.
        Мусор в границе — 422 ``invalid_period``.
        """
        tz = clinic_tz(await self._appointments.clinic_timezone(clinic_id))
        try:
            start = period_start(date_from, tz) if date_from else None
            before = period_end(date_to, tz) if date_to else None
        except InvalidPeriodBoundError as exc:
            raise ValidationAppError(
                f"Не разобрать границу периода «{exc}»: нужна дата YYYY-MM-DD или дата-время ISO",
                code="invalid_period",
            ) from exc
        items, total = await self._appointments.list_page(
            clinic_id,
            patient_id=patient_id,
            status=status,
            from_dt=start,
            before=before,
            page=page,
            page_size=page_size,
        )
        return ListAppointmentsResult(items, total, page, page_size)
