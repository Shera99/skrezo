"""Роут: GET /clinic/schedule/day — календарь дня (view_all → все, view_own → своё)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.modules.schedule.application.use_cases.get_day import GetDayUseCase
from app.modules.schedule.presentation.access import (
    ScheduleScope,
    require_schedule_read,
    resolve_scope_doctor,
)
from app.modules.schedule.presentation.deps import get_get_day_use_case
from app.modules.schedule.presentation.schemas import ScheduleDayResponse

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Право view_own: запрошена чужая колонка",
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
        "description": "Клиника не найдена либо врач не из этой клиники",
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
    "/clinic/schedule/day",
    response_model=ScheduleDayResponse,
    response_description="Сетка дня: часы клиники, обед, колонки врачей и карточки приёмов",
    summary="Календарь дня («лента дня»)",
    responses=_ERRORS,
)
async def get_day(
    day: date = Query(alias="date", description="Дата дня в таймзоне клиники (YYYY-MM-DD)"),
    doctor_id: uuid.UUID | None = Query(
        default=None, alias="doctorId", description="Фильтр по врачу; пусто — все врачи"
    ),
    scope: ScheduleScope = Depends(require_schedule_read),
    use_case: GetDayUseCase = Depends(get_get_day_use_case),
) -> ScheduleDayResponse:
    """Всё для отрисовки экрана «Сегодня» за один запрос.

    Колонки — активные врачи клиники (членство со специализацией) с рабочим окном
    и счётчиком приёмов за день. Карточка приёма несёт имя пациента, услугу и
    интервал ``startAt``–``endAt``. Общая полоса обеда — в поле ``break``.

    С правом ``schedule.view_all`` видны все врачи (``doctorId`` — необязательный
    фильтр). С правом ``schedule.view_own`` виден только сам врач: ответ всегда
    сведён к своей колонке, а чужой ``doctorId`` → 403.

    Границы дня считаются в таймзоне клиники (поле ``timezone``), а не в UTC.
    """
    doctor_id = resolve_scope_doctor(scope, doctor_id)
    calendar = await use_case.execute(scope.clinic_id, day, doctor_id)
    return ScheduleDayResponse.from_calendar(calendar)
