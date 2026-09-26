"""Роут: POST /clinic/staff/{membership_id}/restore — вернуть исключённого (staff.manage)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.staff.application.use_cases.restore_staff import RestoreStaffUseCase
from app.modules.staff.presentation.deps import get_restore_staff_use_case
from app.modules.staff.presentation.schemas import StaffMemberOut
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Нет права staff.manage"},
    404: {
        "description": "Сотрудник не найден в этой клинике",
        "content": {
            "application/json": {
                "example": {"error": "staff_not_found", "message": "Сотрудник не найден"}
            }
        },
    },
    409: {
        "description": "Свободных мест в тарифе нет",
        "content": {
            "application/json": {
                "example": {
                    "error": "seats_limit_reached",
                    "message": "Достигнут лимит мест по тарифу",
                }
            }
        },
    },
}


@router.post(
    "/clinic/staff/{membership_id}/restore",
    response_model=StaffMemberOut,
    summary="Вернуть исключённого сотрудника",
    response_description="Сотрудник снова в клинике — с прежней ролью и специальностью",
    responses=_ERRORS,
)
async def restore_staff(
    membership_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("staff.manage")),
    use_case: RestoreStaffUseCase = Depends(get_restore_staff_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> StaffMemberOut:
    """Включить обратно членство, закрытое `DELETE /clinic/staff/{id}`.

    Удаление — деактивация, а завести того же человека заново нельзя: номер
    занят его аккаунтом (409 `phone_already_used`). Восстановление возвращает то
    же членство с прежней ролью и специальностью, история приёмов на месте.
    Сотрудник занимает место в тарифе — нет свободного, 409 `seats_limit_reached`.
    Уже активный сотрудник возвращается как есть.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    member = await use_case.execute(context.clinic_id, membership_id)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="staff.restore",
        entity_type="membership",
        entity_id=membership_id,
        payload={},
    )
    return StaffMemberOut.from_member(member)
