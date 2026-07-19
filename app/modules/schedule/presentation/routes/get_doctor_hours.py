"""Роут: GET /clinic/schedule/doctors/{id}/hours — график врача (view_all/own)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.schedule.application.use_cases.get_doctor_hours import GetDoctorHoursUseCase
from app.modules.schedule.presentation.access import (
    ScheduleScope,
    require_schedule_read,
    resolve_scope_doctor,
)
from app.modules.schedule.presentation.deps import get_get_doctor_hours_use_case
from app.modules.schedule.presentation.schemas import DoctorHoursItem, DoctorHoursResponse

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Право view_own: запрошен чужой врач",
        "content": {
            "application/json": {
                "example": {
                    "error": "not_own_schedule",
                    "message": "Доступно только своё расписание",
                }
            }
        },
    },
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
    scope: ScheduleScope = Depends(require_schedule_read),
    use_case: GetDoctorHoursUseCase = Depends(get_get_doctor_hours_use_case),
) -> DoctorHoursResponse:
    """Индивидуальный график врача по дням недели.

    Всегда возвращаются все 7 дней: дни без собственной настройки отдаются по
    часам клиники — врач работает вместе с ней.

    С правом ``schedule.view_all`` доступен график любого врача клиники, с правом
    ``schedule.view_own`` — только собственный (чужой ``membershipId`` → 403).
    """
    membership_id = resolve_scope_doctor(scope, membership_id) or membership_id
    days = await use_case.execute(scope.clinic_id, membership_id)
    return DoctorHoursResponse(days=[DoctorHoursItem.from_hours(d) for d in days])
