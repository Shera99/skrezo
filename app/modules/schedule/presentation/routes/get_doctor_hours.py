"""Роут: GET /clinic/schedule/doctors/{id}/hours — график врача (schedule.view_all)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.schedule.application.use_cases.get_doctor_hours import GetDoctorHoursUseCase
from app.modules.schedule.presentation.deps import get_get_doctor_hours_use_case
from app.modules.schedule.presentation.schemas import DoctorHoursItem, DoctorHoursResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Врач не найден в этой клинике",
        "content": {
            "application/json": {
                "example": {
                    "error": "doctor_not_found",
                    "message": "Врач не найден в этой клинике",
                }
            }
        },
    },
}


@router.get(
    "/clinic/schedule/doctors/{membership_id}/hours",
    response_model=DoctorHoursResponse,
    response_description="Ровно 7 дней недели (0=пн … 6=вс)",
    summary="График врача",
    responses=_ERRORS,
)
async def get_doctor_hours(
    membership_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("schedule.view_all")),
    use_case: GetDoctorHoursUseCase = Depends(get_get_doctor_hours_use_case),
) -> DoctorHoursResponse:
    """Индивидуальный график врача по дням недели.

    Всегда возвращаются все 7 дней: дни без собственной настройки отдаются по
    часам клиники — врач работает вместе с ней.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    days = await use_case.execute(context.clinic_id, membership_id)
    return DoctorHoursResponse(days=[DoctorHoursItem.from_hours(d) for d in days])
