"""Роут: POST /clinic/staff — добавить работника (право staff.manage)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.staff.application.use_cases.add_staff import AddStaffUseCase
from app.modules.staff.presentation.deps import get_add_staff_use_case
from app.modules.staff.presentation.schemas import AddStaffRequest, StaffCreatedResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Нет права staff.manage"},
    409: {
        "description": "Телефон занят или лимит мест",
        "content": {
            "application/json": {
                "examples": {
                    "phone": {"value": {"error": "phone_already_used"}},
                    "seats": {"value": {"error": "seats_limit_reached"}},
                }
            }
        },
    },
}


@router.post(
    "/clinic/staff",
    status_code=status.HTTP_201_CREATED,
    response_model=StaffCreatedResponse,
    summary="Добавить работника",
    response_description="membershipId созданного работника",
    responses=_ERRORS,
)
async def add_staff(
    body: AddStaffRequest,
    context: ClientContext = Depends(require_permission("staff.manage")),
    use_case: AddStaffUseCase = Depends(get_add_staff_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> StaffCreatedResponse:
    """Завести работника клиники.

    Пароль не выдаётся: работник входит по коду подтверждения на свой номер, а
    сообщение о добавлении уходит ему автоматически.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    result = await use_case.execute(
        context.clinic_id,
        full_name=body.full_name,
        phone_raw=body.phone,
        specialization_id=body.specialization_id,
        role_id=body.role_id,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="staff.add",
        entity_type="membership",
        entity_id=result.membership.id,
        payload={"roleId": str(result.membership.role_id)},
    )
    return StaffCreatedResponse(membership_id=result.membership.id)
