"""Роут: PUT /clinic/schedule/doctors/{id}/hours — задать график врача (schedule.manage_all)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.schedule.application.use_cases.set_doctor_hours import SetDoctorHoursUseCase
from app.modules.schedule.presentation.deps import get_set_doctor_hours_use_case
from app.modules.schedule.presentation.schemas import (
    DoctorHoursItem,
    DoctorHoursResponse,
    SetDoctorHoursRequest,
)
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
    context: ClientContext = Depends(require_permission("schedule.manage_all")),
    use_case: SetDoctorHoursUseCase = Depends(get_set_doctor_hours_use_case),
) -> DoctorHoursResponse:
    """Заменить индивидуальный график врача: все 7 дней передаются разом.

    График **сужает** часы клиники: реальное окно приёма = пересечение часов
    клиники и этого графика. Уже созданные приёмы не пересчитываются.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    saved = await use_case.execute(
        context.clinic_id, membership_id, [d.to_hours() for d in body.days]
    )
    return DoctorHoursResponse(days=[DoctorHoursItem.from_hours(d) for d in saved])
