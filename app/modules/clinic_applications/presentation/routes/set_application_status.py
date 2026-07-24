"""Роут: PATCH /admin/clinic-applications/{id}/status — статус заявки (только Skrezo)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clinic_applications.application.use_cases.set_application_status import (
    SetApplicationStatusUseCase,
)
from app.modules.clinic_applications.presentation.deps import (
    get_set_application_status_use_case,
)
from app.modules.clinic_applications.presentation.schemas import (
    ApplicationItem,
    SetApplicationStatusRequest,
)
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Заявка не найдена",
        "content": {
            "application/json": {
                "example": {"error": "application_not_found", "message": "Заявка не найдена"}
            }
        },
    },
}


@router.patch(
    "/admin/clinic-applications/{application_id}/status",
    response_model=ApplicationItem,
    response_description="Заявка с новым статусом и отметкой, кто его поставил",
    summary="Сменить статус заявки",
    responses=_ERRORS,
)
async def set_application_status(
    application_id: uuid.UUID,
    body: SetApplicationStatusRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: SetApplicationStatusUseCase = Depends(get_set_application_status_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ApplicationItem:
    """Поставить заявке статус: ``pending`` | ``reviewed`` | ``rejected``.

    Запоминается, кто из команды Skrezo и когда сменил статус; переходы свободные
    (решение можно пересмотреть). Действие пишется в аудит платформы.
    """
    row = await use_case.execute(application_id, body.status, actor)
    await audit.record(
        actor_id=actor,
        action="clinic_application.status_changed",
        entity_type="clinic_application",
        entity_id=application_id,
        payload={"status": body.status.value},
    )
    return ApplicationItem.from_row(row)
