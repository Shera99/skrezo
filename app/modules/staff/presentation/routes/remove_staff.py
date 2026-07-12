"""Роут: DELETE /clinic/staff/{membership_id} — удалить работника (право staff.manage)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.staff.application.use_cases.remove_staff import RemoveStaffUseCase
from app.modules.staff.presentation.deps import get_remove_staff_use_case
from app.modules.staff.presentation.schemas import StaffRemovedResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Нет права staff.manage или это владелец"},
    404: {"description": "Сотрудник не найден"},
}


@router.delete(
    "/clinic/staff/{membership_id}",
    response_model=StaffRemovedResponse,
    summary="Удалить работника (деактивация)",
    responses=_ERRORS,
)
async def remove_staff(
    membership_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("staff.manage")),
    use_case: RemoveStaffUseCase = Depends(get_remove_staff_use_case),
) -> StaffRemovedResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    mode = await use_case.execute(context.clinic_id, membership_id)
    return StaffRemovedResponse(mode=mode)
