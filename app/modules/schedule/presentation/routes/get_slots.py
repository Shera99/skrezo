"""Роут: GET /clinic/schedule/slots — свободные слоты врача (schedule.view_all)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.schedule.application.use_cases.get_slots import GetSlotsUseCase
from app.modules.schedule.presentation.deps import get_get_slots_use_case
from app.modules.schedule.presentation.schemas import ScheduleSlotsResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {
        "description": "Врач или услуга не из этой клиники",
        "content": {
            "application/json": {
                "example": {"error": "invalid_doctor", "message": "Врач не из этой клиники"}
            }
        },
    },
}


@router.get(
    "/clinic/schedule/slots",
    response_model=ScheduleSlotsResponse,
    response_description="Свободные интервалы врача на указанный день",
    summary="Свободные слоты врача на день",
    responses=_ERRORS,
)
async def get_slots(
    doctor_id: uuid.UUID = Query(alias="doctorId", description="Членство врача в этой клинике"),
    day: date = Query(alias="date", description="Дата в таймзоне клиники (YYYY-MM-DD)"),
    service_id: uuid.UUID | None = Query(
        default=None,
        alias="serviceId",
        description="Услуга: её длительность задаёт размер слота",
    ),
    context: ClientContext = Depends(require_permission("schedule.view_all")),
    use_case: GetSlotsUseCase = Depends(get_get_slots_use_case),
) -> ScheduleSlotsResponse:
    """Слоты, в которые врача реально можно записать.

    Учитываются часы работы клиники, общий обед, индивидуальный график врача,
    его отсутствия и уже занятые приёмы (отменённые слот не занимают).

    Длительность слота — из ``Service.duration`` при переданном ``serviceId``,
    иначе 30 минут. Слоты нарезаются с шагом 15 минут.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    slots = await use_case.execute(context.clinic_id, doctor_id, day, service_id)
    return ScheduleSlotsResponse.from_day_slots(slots)
