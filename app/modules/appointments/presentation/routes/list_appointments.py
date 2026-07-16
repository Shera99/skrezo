"""Роут: GET /clinic/appointments — приёмы клиники (patients.view_medical)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.appointments.application.use_cases.list_appointments import ListAppointmentsUseCase
from app.modules.appointments.presentation.deps import get_list_appointments_use_case
from app.modules.appointments.presentation.schemas import (
    AppointmentListItem,
    AppointmentsResponse,
)
from app.shared.auth import ClientContext
from app.shared.db.enums import AppointmentStatus

router = APIRouter()
_PAGE_SIZE = 25


@router.get(
    "/clinic/appointments",
    response_model=AppointmentsResponse,
    summary="Приёмы клиники (фильтры + пагинация)",
)
async def list_appointments(
    patient_id: uuid.UUID | None = Query(default=None, alias="patientId"),
    appt_status: AppointmentStatus | None = Query(default=None, alias="status"),
    date_from: datetime | None = Query(default=None, alias="from"),
    date_to: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    context: ClientContext = Depends(require_permission("patients.view_medical")),
    use_case: ListAppointmentsUseCase = Depends(get_list_appointments_use_case),
) -> AppointmentsResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    result = await use_case.execute(
        context.clinic_id,
        patient_id=patient_id,
        status=appt_status,
        from_dt=date_from,
        to_dt=date_to,
        page=page,
        page_size=_PAGE_SIZE,
    )
    return AppointmentsResponse(
        list=[AppointmentListItem.from_row(r) for r in result.items],
        current_page=result.current_page,
        page_size=result.page_size,
        total_count=result.total_count,
    )
