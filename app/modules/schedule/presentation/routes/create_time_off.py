"""Роут: POST /clinic/schedule/doctors/{id}/time-off — завести отсутствие (manage_all/own)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.modules.schedule.application.use_cases.create_time_off import CreateTimeOffUseCase
from app.modules.schedule.presentation.access import (
    ScheduleScope,
    require_schedule_manage,
    resolve_scope_doctor,
)
from app.modules.schedule.presentation.deps import get_create_time_off_use_case
from app.modules.schedule.presentation.schemas import CreateTimeOffRequest, TimeOffItem

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Право manage_own: попытка завести отсутствие чужому врачу",
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
    422: {
        "description": "Некорректный интервал: конец не позже начала",
        "content": {
            "application/json": {
                "example": {
                    "error": "invalid_time_off",
                    "message": "Конец отсутствия должен быть позже начала",
                }
            }
        },
    },
}


@router.post(
    "/clinic/schedule/doctors/{membership_id}/time-off",
    response_model=TimeOffItem,
    status_code=status.HTTP_201_CREATED,
    response_description="Созданное отсутствие",
    summary="Завести отсутствие врача",
    responses=_ERRORS,
)
async def create_time_off(
    membership_id: uuid.UUID,
    body: CreateTimeOffRequest,
    scope: ScheduleScope = Depends(require_schedule_manage),
    use_case: CreateTimeOffUseCase = Depends(get_create_time_off_use_case),
) -> TimeOffItem:
    """Отпуск, больничный или разовая недоступность врача.

    Интервал абсолютный (aware) и перекрывает и график врача, и часы клиники:
    пересечение делает слот недоступным (учитывается в ``/slots`` и «ленте дня»).
    Уже созданные приёмы не пересчитываются.

    С правом ``schedule.manage_all`` можно завести отсутствие любому врачу клиники,
    с правом ``schedule.manage_own`` — только себе (чужой ``membershipId`` → 403).
    """
    membership_id = resolve_scope_doctor(scope, membership_id) or membership_id
    record = await use_case.execute(
        scope.clinic_id, membership_id, body.starts_at, body.ends_at, body.reason
    )
    return TimeOffItem.from_record(record)
