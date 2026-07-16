"""Use-case: список приёмов клиники (фильтры + offset-пагинация)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.modules.appointments.domain.models import AppointmentRow
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
        from_dt: datetime | None,
        to_dt: datetime | None,
        page: int,
        page_size: int,
    ) -> ListAppointmentsResult:
        items, total = await self._appointments.list_page(
            clinic_id,
            patient_id=patient_id,
            status=status,
            from_dt=from_dt,
            to_dt=to_dt,
            page=page,
            page_size=page_size,
        )
        return ListAppointmentsResult(items, total, page, page_size)
