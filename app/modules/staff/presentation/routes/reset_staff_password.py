"""Роут: POST /clinic/staff/{id}/reset-password — сброс пароля работника."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.staff.application.use_cases.reset_staff_password import ResetStaffPasswordUseCase
from app.modules.staff.presentation.deps import get_reset_staff_password_use_case
from app.modules.staff.presentation.schemas import StaffPasswordResetResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Нет права staff.manage или это владелец"},
    404: {"description": "Сотрудник не найден"},
}


@router.post(
    "/clinic/staff/{membership_id}/reset-password",
    response_model=StaffPasswordResetResponse,
    summary="Сбросить пароль работника",
    response_description="Новый временный пароль работника",
    responses=_ERRORS,
)
async def reset_staff_password(
    membership_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("staff.manage")),
    use_case: ResetStaffPasswordUseCase = Depends(get_reset_staff_password_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> StaffPasswordResetResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    temp_password = await use_case.execute(context.clinic_id, membership_id)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="staff.password_reset",
        entity_type="membership",
        entity_id=membership_id,
        payload=None,
    )
    return StaffPasswordResetResponse(temp_password=temp_password)
