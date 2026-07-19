"""Роут: GET /clinic/schedule/doctors/{id}/time-off — отсутствия врача (view_all/own)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.schedule.application.use_cases.list_time_off import ListTimeOffUseCase
from app.modules.schedule.presentation.access import (
    ScheduleScope,
    require_schedule_read,
    resolve_scope_doctor,
)
from app.modules.schedule.presentation.deps import get_list_time_off_use_case
from app.modules.schedule.presentation.schemas import TimeOffListResponse

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
    "/clinic/schedule/doctors/{membership_id}/time-off",
    response_model=TimeOffListResponse,
    response_description="Все отсутствия врача по возрастанию начала",
    summary="Отсутствия врача",
    responses=_ERRORS,
)
async def list_time_off(
    membership_id: uuid.UUID,
    scope: ScheduleScope = Depends(require_schedule_read),
    use_case: ListTimeOffUseCase = Depends(get_list_time_off_use_case),
) -> TimeOffListResponse:
    """Список отсутствий врача (отпуска, больничные) в таймзоне клиники.

    С правом ``schedule.view_all`` доступны отсутствия любого врача клиники, с
    правом ``schedule.view_own`` — только свои (чужой ``membershipId`` → 403).
    """
    membership_id = resolve_scope_doctor(scope, membership_id) or membership_id
    result = await use_case.execute(scope.clinic_id, membership_id)
    return TimeOffListResponse.from_list(result)
