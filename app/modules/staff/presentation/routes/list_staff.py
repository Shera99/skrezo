"""Роут: GET /clinic/staff — список сотрудников (право staff.view)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.staff.application.use_cases.list_staff import ListStaffUseCase
from app.modules.staff.presentation.deps import get_list_staff_use_case
from app.modules.staff.presentation.schemas import StaffMemberOut
from app.shared.auth import ClientContext

router = APIRouter()


@router.get(
    "/clinic/staff",
    response_model=list[StaffMemberOut],
    summary="Сотрудники клиники",
)
async def list_staff(
    context: ClientContext = Depends(require_permission("staff.view")),
    use_case: ListStaffUseCase = Depends(get_list_staff_use_case),
) -> list[StaffMemberOut]:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    members = await use_case.execute(context.clinic_id)
    return [StaffMemberOut.from_member(m) for m in members]
