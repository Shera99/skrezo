"""Роут: GET /clinic/schedule/day — календарь дня (schedule.view_all)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.schedule.application.use_cases.get_day import GetDayUseCase
from app.modules.schedule.presentation.deps import get_get_day_use_case
from app.modules.schedule.presentation.schemas import ScheduleDayResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Клиника не найдена",
        "content": {
            "application/json": {
                "example": {"error": "clinic_not_found", "message": "Клиника не найдена"}
            }
        },
    },
    422: {
        "description": "Врач не из этой клиники",
        "content": {
            "application/json": {
                "example": {"error": "invalid_doctor", "message": "Врач не из этой клиники"}
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
    context: ClientContext = Depends(require_permission("schedule.view_all")),
    use_case: GetDayUseCase = Depends(get_get_day_use_case),
) -> ScheduleDayResponse:
    """Всё для отрисовки экрана «Сегодня» за один запрос.

    Колонки — активные врачи клиники (членство со специализацией) с рабочим окном
    и счётчиком приёмов за день. Карточка приёма несёт имя пациента, услугу и
    интервал ``startAt``–``endAt``. Общая полоса обеда — в поле ``break``.

    Границы дня считаются в таймзоне клиники (поле ``timezone``), а не в UTC.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    calendar = await use_case.execute(context.clinic_id, day, doctor_id)
    return ScheduleDayResponse.from_calendar(calendar)
