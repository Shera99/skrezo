"""Роут: GET /clinic/schedule/slots — свободные слоты врача (view_all → любой, view_own → свой)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.modules.schedule.application.use_cases.get_slots import GetSlotsUseCase
from app.modules.schedule.presentation.access import (
    ScheduleScope,
    require_schedule_read,
    resolve_scope_doctor,
)
from app.modules.schedule.presentation.deps import get_get_slots_use_case
from app.modules.schedule.presentation.schemas import ScheduleSlotsResponse

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
        "description": "Врач не найден в этой клинике (или врача нет вовсе)",
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
        "description": "Услуга не из этой клиники",
        "content": {
            "application/json": {
                "example": {"error": "invalid_service", "message": "Услуга не из этой клиники"}
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
    day: date = Query(alias="date", description="Дата в таймзоне клиники (YYYY-MM-DD)"),
    doctor_id: uuid.UUID | None = Query(
        default=None,
        alias="doctorId",
        description=(
            "Членство врача в этой клинике. Можно не передавать, если задан "
            "`serviceId` — тогда слоты собираются по всем, кто её выполняет"
        ),
    ),
    service_id: uuid.UUID | None = Query(
        default=None,
        alias="serviceId",
        description=(
            "Услуга: её длительность задаёт размер слота, а без `doctorId` — "
            "ещё и круг врачей, среди которых искать"
        ),
    ),
    scope: ScheduleScope = Depends(require_schedule_read),
    use_case: GetSlotsUseCase = Depends(get_get_slots_use_case),
) -> ScheduleSlotsResponse:
    """Слоты, в которые реально можно записать.

    Учитываются часы работы клиники, общий обед, индивидуальный график врача,
    его отсутствия и уже занятые приёмы (отменённые слот не занимают).

    **По врачу** — передайте `doctorId`. **По услуге** — передайте только
    `serviceId`: вернутся слоты всех врачей, которые её выполняют, отсортированные
    по времени, и у каждого слота свой `doctorId`. Это ответ на вопрос «когда
    ближайшее свободное на эту услугу» одним запросом вместо опроса врачей по
    очереди. Услуга без назначенных врачей (`doctorIds: null`) считается общей —
    в подбор идут все практикующие клиники.

    Не передать ни того, ни другого нельзя (422 `doctor_or_service_required`):
    без них это сетка регистратуры, а не подбор времени для записи.

    С правом ``schedule.view_all`` доступны слоты любого врача клиники. С правом
    ``schedule.view_own`` — только собственные: и явный `doctorId` коллеги (403),
    и подбор по услуге сводятся к своему членству.

    Длительность слота — из ``Service.duration`` при переданном ``serviceId``,
    иначе 30 минут. Слоты нарезаются с шагом 15 минут.
    """
    # own-контур: и явный doctorId, и подбор по услуге сводим к своему членству,
    # иначе врач увидел бы свободное время коллег в обход 403 на явный запрос.
    doctor_id = resolve_scope_doctor(scope, doctor_id) or doctor_id
    slots = await use_case.execute(scope.clinic_id, doctor_id, day, service_id)
    return ScheduleSlotsResponse.from_day_slots(slots)
