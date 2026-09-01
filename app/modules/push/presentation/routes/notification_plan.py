"""Роут: GET /clinic/notifications/plan — серверный план локальных напоминаний.

Контракт совпадает с клиентским ``PlannedNotify`` (ТЗ-СУ §8): на устройстве
меняется только источник данных, локальный ``planner.ts`` остаётся фолбэком.
RBAC — контур расписания: гварды переиспользуются из модуля schedule (это
общие scope-зависимости, как ``require_permission``, а не его ручки).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.errors import ValidationAppError
from app.modules.push.application.use_cases.get_notification_plan import (
    GetNotificationPlanUseCase,
)
from app.modules.push.presentation.deps import get_notification_plan_use_case
from app.modules.push.presentation.schemas import NotificationPlanResponse
from app.modules.schedule.presentation.access import (
    ScheduleScope,
    require_schedule_read,
    resolve_scope_doctor,
)
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

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
    404: {"description": "Врач не найден в этой клинике"},
}


@router.get(
    "/clinic/notifications/plan",
    response_model=NotificationPlanResponse,
    summary="План локальных напоминаний на день",
    response_description="Напоминания soon/arrival с готовыми текстами",
    responses=_ERRORS,
)
async def get_notification_plan(
    day: date = Query(alias="date", description="Дата в таймзоне клиники (YYYY-MM-DD)"),
    doctor_id: uuid.UUID | None = Query(
        default=None,
        alias="doctorId",
        description=(
            "Членство врача. С правом `view_own` можно не передавать — "
            "подставится своё; чужой врач при `view_own` → 403"
        ),
    ),
    lang: Lang = Depends(get_request_lang),
    scope: ScheduleScope = Depends(require_schedule_read),
    use_case: GetNotificationPlanUseCase = Depends(get_notification_plan_use_case),
) -> NotificationPlanResponse:
    """Сервер решает, устройство исполняет (ADR-5).

    На каждый приём дня со статусом `scheduled`/`confirmed` — два напоминания:
    `soon` за 15 минут до начала и `arrival` через 5 после (если врач не
    отметил приход — уведомление подтолкнёт). Пришедшие, идущие, завершённые и
    отменённые приёмы в план не попадают. Тексты (`title`/`body`/`speak`)
    готовы к показу на языке запроса.

    Горизонт — один день. Устройство перечитывает план при выходе на передний
    план, по событию сокета и периодически; `generatedAt` старше 6 часов —
    клиент деградирует тексты до «Проверьте расписание». Идентификаторы
    стабильны (`soon:<appointmentId>`) — повторная загрузка заменяет
    запланированное, а не дублирует.
    """
    resolved = resolve_scope_doctor(scope, doctor_id) or doctor_id
    if resolved is None:
        # view_all без doctorId: план — вещь персональная, «за всех» его нет.
        raise ValidationAppError(
            "Укажите врача: план напоминаний считается на одного врача",
            code="doctor_required",
        )
    plan = await use_case.execute(scope.clinic_id, resolved, day, lang)
    return NotificationPlanResponse.from_plan(plan)
