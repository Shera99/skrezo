"""Роут: POST /clinic/staff/{id}/reset-password — сброс пароля работника."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
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
) -> StaffPasswordResetResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    temp_password = await use_case.execute(context.clinic_id, membership_id)
    return StaffPasswordResetResponse(temp_password=temp_password)
