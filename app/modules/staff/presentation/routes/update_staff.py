"""Роут: PUT /clinic/staff/{membership_id} — правка работника (право staff.manage).

Смену телефона работника делает владелец/менеджер; сам работник не может.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.staff.application.use_cases.update_staff import UpdateStaffUseCase
from app.modules.staff.presentation.deps import get_update_staff_use_case
from app.modules.staff.presentation.schemas import StaffMemberOut, UpdateStaffRequest
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Нет права staff.manage или это владелец"},
    404: {"description": "Сотрудник не найден"},
    409: {"description": "Телефон занят"},
}


@router.put(
    "/clinic/staff/{membership_id}",
    response_model=StaffMemberOut,
    summary="Правка работника (в т.ч. телефона)",
    responses=_ERRORS,
)
async def update_staff(
    membership_id: uuid.UUID,
    body: UpdateStaffRequest,
    context: ClientContext = Depends(require_permission("staff.manage")),
    use_case: UpdateStaffUseCase = Depends(get_update_staff_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> StaffMemberOut:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    member = await use_case.execute(
        context.clinic_id,
        membership_id,
        full_name=body.full_name,
        phone_raw=body.phone,
        specialization_id=body.specialization_id,
        role_id=body.role_id,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="staff.update",
        entity_type="membership",
        entity_id=membership_id,
        payload={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    return StaffMemberOut.from_member(member)
