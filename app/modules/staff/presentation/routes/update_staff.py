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
from app.modules.staff.application.use_cases.update_staff import UNSET, UpdateStaffUseCase
from app.modules.staff.presentation.deps import get_update_staff_use_case
from app.modules.staff.presentation.schemas import StaffMemberOut, UpdateStaffRequest
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Нет права staff.manage или это владелец"},
    404: {"description": "Сотрудник не найден"},
    409: {
        "description": (
            "Телефон занят (`phone_already_used`) или у врача есть будущие записи, а "
            "специальность снимают (`doctor_has_future_appointments`): календарь показывает "
            "только сотрудников со специальностью, и записи пропали бы из расписания"
        ),
        "content": {
            "application/json": {
                "example": {
                    "error": "doctor_has_future_appointments",
                    "message": (
                        "У врача 2 будущих записей — перенесите их на другого врача, "
                        "потом снимите специальность"
                    ),
                    "count": 2,
                    "appointments": [
                        {
                            "id": "0d1e2f3a-4b5c-4d6e-8f7a-9b0c1d2e3f4a",
                            "scheduledAt": "2026-10-01T10:00:00+00:00",
                            "patientName": "Асанова Айгүл",
                        }
                    ],
                }
            }
        },
    },
    422: {"description": "`null` в ФИО, телефоне или роли — эти поля обязательны"},
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
    """Правка работника: ФИО, телефон, специальность, роль — только переданные поля.

    `specializationId: null` снимает специальность: сотрудник перестаёт
    принимать пациентов (например, врач стал администратором). У врача с
    будущими записями — 409 `doctor_has_future_appointments` со списком записей:
    сначала их переносят на другого врача. У владельца здесь меняется только
    специальность.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    # Специальность: отсутствие поля и явный null — разные намерения
    # (не трогать против снять), поэтому смотрим, что именно прислали.
    specialization = (
        body.specialization_id if "specialization_id" in body.model_fields_set else UNSET
    )
    member = await use_case.execute(
        context.clinic_id,
        membership_id,
        full_name=body.full_name,
        phone_raw=body.phone,
        specialization_id=specialization,
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
