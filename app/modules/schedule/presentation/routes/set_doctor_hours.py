"""Роут: PUT /clinic/schedule/doctors/{id}/hours — график врача (manage_all/own)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.schedule.application.use_cases.set_doctor_hours import SetDoctorHoursUseCase
from app.modules.schedule.presentation.access import (
    ScheduleScope,
    require_schedule_manage,
    resolve_scope_doctor,
)
from app.modules.schedule.presentation.deps import get_set_doctor_hours_use_case
from app.modules.schedule.presentation.schemas import (
    DoctorHoursItem,
    DoctorHoursResponse,
    SetDoctorHoursRequest,
)

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Право manage_own: попытка править чужой график",
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
        "description": "Некорректная сетка: не 7 дней или перепутаны границы смены",
        "content": {
            "application/json": {
                "example": {
                    "error": "invalid_doctor_hours",
                    "message": "День 0: начало смены должно быть раньше конца",
                }
            }
        },
    },
}


@router.put(
    "/clinic/schedule/doctors/{membership_id}/hours",
    response_model=DoctorHoursResponse,
    response_description="Сохранённый график врача (7 дней)",
    summary="Задать график врача",
    responses=_ERRORS,
)
async def set_doctor_hours(
    membership_id: uuid.UUID,
    body: SetDoctorHoursRequest,
    scope: ScheduleScope = Depends(require_schedule_manage),
    use_case: SetDoctorHoursUseCase = Depends(get_set_doctor_hours_use_case),
) -> DoctorHoursResponse:
    """Заменить индивидуальный график врача: все 7 дней передаются разом.

    С правом ``schedule.manage_all`` можно править график любого врача клиники,
    с правом ``schedule.manage_own`` — только свой (чужой ``membershipId`` → 403).

    График **сужает** часы клиники: реальное окно приёма = пересечение часов
    клиники и этого графика. Уже созданные приёмы не пересчитываются.
    """
    membership_id = resolve_scope_doctor(scope, membership_id) or membership_id
    saved = await use_case.execute(
        scope.clinic_id, membership_id, [d.to_hours() for d in body.days]
    )
    return DoctorHoursResponse(days=[DoctorHoursItem.from_hours(d) for d in saved])
