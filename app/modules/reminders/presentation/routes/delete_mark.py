"""Роут: снять отметку «напомнили», поставленную по ошибке."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.reminders.application.use_cases.delete_reminder_mark import (
    DeleteReminderMarkUseCase,
)
from app.modules.reminders.presentation.deps import get_delete_reminder_mark_use_case
from app.modules.reminders.presentation.schemas import ReminderMarkDeletedResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Отметка не найдена (чужая клиника или неверный id)",
        "content": {
            "application/json": {
                "example": {"error": "reminder_mark_not_found", "message": "Отметка не найдена"}
            }
        },
    },
}


@router.delete(
    "/clinic/reminders/marks/{mark_id}",
    response_model=ReminderMarkDeletedResponse,
    response_description="Отметка удалена",
    summary="Снять отметку «напомнили»",
    responses=_ERRORS,
)
async def delete_mark(
    mark_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("reminders.manage")),
    use_case: DeleteReminderMarkUseCase = Depends(get_delete_reminder_mark_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ReminderMarkDeletedResponse:
    """Удалить отметку по id — когда отметили не того пациента или не тот повод.

    Удаление скоупится клиникой из токена: чужую отметку не снять (404).
    Факт попадает в журнал действий клиники.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    await use_case.execute(context.clinic_id, mark_id)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="reminders.mark_remove",
        entity_type="reminder_mark",
        entity_id=mark_id,
    )
    return ReminderMarkDeletedResponse(id=mark_id)
