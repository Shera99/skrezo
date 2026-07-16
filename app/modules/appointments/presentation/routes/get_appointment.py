"""Роут: GET /clinic/appointments/{id} — детали приёма (patients.view_medical)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.appointments.application.use_cases.get_appointment import GetAppointmentUseCase
from app.modules.appointments.presentation.deps import get_get_appointment_use_case
from app.modules.appointments.presentation.schemas import AppointmentDetailResponse
from app.shared.auth import ClientContext

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Приём не найден"}}


@router.get(
    "/clinic/appointments/{appointment_id}",
    response_model=AppointmentDetailResponse,
    summary="Детали приёма (зубы, услуги)",
    responses=_NOT_FOUND,
)
async def get_appointment(
    appointment_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("patients.view_medical")),
    use_case: GetAppointmentUseCase = Depends(get_get_appointment_use_case),
) -> AppointmentDetailResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    detail = await use_case.execute(context.clinic_id, appointment_id)
    return AppointmentDetailResponse.from_detail(detail)
