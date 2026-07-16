"""Роут: PATCH /clinic/appointments/{id} — обновить приём (appointments.edit)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.appointments.application.use_cases.update_appointment import (
    UpdateAppointmentUseCase,
)
from app.modules.appointments.presentation.deps import get_update_appointment_use_case
from app.modules.appointments.presentation.schemas import (
    AppointmentDetailResponse,
    UpdateAppointmentRequest,
)
from app.shared.auth import ClientContext

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Приём не найден"}}


@router.patch(
    "/clinic/appointments/{appointment_id}",
    response_model=AppointmentDetailResponse,
    summary="Обновить приём (статус/комментарий/врач/дата)",
    responses=_NOT_FOUND,
)
async def update_appointment(
    appointment_id: uuid.UUID,
    body: UpdateAppointmentRequest,
    context: ClientContext = Depends(require_permission("appointments.edit")),
    use_case: UpdateAppointmentUseCase = Depends(get_update_appointment_use_case),
) -> AppointmentDetailResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    detail = await use_case.execute(
        context.clinic_id, appointment_id, body.model_dump(exclude_unset=True)
    )
    return AppointmentDetailResponse.from_detail(detail)
