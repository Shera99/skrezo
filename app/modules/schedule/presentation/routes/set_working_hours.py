"""Роут: PUT /clinic/schedule/working-hours — задать часы клиники (settings.edit)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.push.application.emitter import PushEmitter
from app.modules.push.presentation.deps import get_push_emitter
from app.modules.schedule.application.use_cases.set_working_hours import SetWorkingHoursUseCase
from app.modules.schedule.presentation.deps import get_set_working_hours_use_case
from app.modules.schedule.presentation.schemas import (
    SetWorkingHoursRequest,
    WorkingHoursItem,
    WorkingHoursResponse,
)
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {
        "description": "Некорректная сетка: не 7 дней, перепутаны границы или обед вне часов",
        "content": {
            "application/json": {
                "example": {
                    "error": "invalid_break",
                    "message": "День 0: обед должен быть внутри рабочих часов",
                }
            }
        },
    },
}


@router.put(
    "/clinic/schedule/working-hours",
    response_model=WorkingHoursResponse,
    response_description="Сохранённые часы работы (7 дней)",
    summary="Задать часы работы клиники",
    responses=_ERRORS,
)
async def set_working_hours(
    body: SetWorkingHoursRequest,
    context: ClientContext = Depends(require_permission("settings.edit")),
    use_case: SetWorkingHoursUseCase = Depends(get_set_working_hours_use_case),
    audit: AuditService = Depends(get_audit_service),
    emitter: PushEmitter = Depends(get_push_emitter),
) -> WorkingHoursResponse:
    """Заменить часы работы клиники: все 7 дней передаются разом.

    Обед (``breakStart``/``breakEnd``) задаётся обеими границами или не задаётся
    вовсе и должен помещаться внутрь рабочих часов. У нерабочего дня
    (``isWorking=false``) время не проверяется.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    saved = await use_case.execute(context.clinic_id, [d.to_hours() for d in body.days])
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="schedule.working_hours",
        entity_type="clinic",
        entity_id=context.clinic_id,
        payload=None,
    )
    # Часы клиники изменились — тихо просим приложения перечитать день (sync.day).
    await emitter.emit_sync_day(context.clinic_id)
    return WorkingHoursResponse(days=[WorkingHoursItem.from_hours(d) for d in saved])
