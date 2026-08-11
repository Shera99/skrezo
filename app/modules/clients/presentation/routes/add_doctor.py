"""Роут: POST /admin/clients/{id}/doctors — добавить врача (админ-сайд).

Занимает 1 место; при заполненных местах — 409 (апселл на след. ступень).
Переиспользует use-case добавления сотрудника из модуля `staff`.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clients.presentation.schemas import DoctorCreatedResponse, DoctorCreateRequest
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.staff.application.use_cases.add_staff import AddStaffUseCase
from app.modules.staff.presentation.deps import get_add_staff_use_case

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Соло не поддерживает сотрудников (solo_no_staff)"},
    404: {"description": "Клиника/специализация не найдена"},
    409: {
        "description": "Телефон занят или достигнут лимит мест",
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
    "/admin/clients/{clinic_id}/doctors",
    status_code=status.HTTP_201_CREATED,
    response_model=DoctorCreatedResponse,
    summary="Добавить врача (админ-сайд)",
    response_description="membershipId созданного врача",
    responses=_ERRORS,
)
async def add_doctor(
    clinic_id: uuid.UUID,
    body: DoctorCreateRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: AddStaffUseCase = Depends(get_add_staff_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> DoctorCreatedResponse:
    result = await use_case.execute(
        clinic_id,
        full_name=body.full_name,
        phone_raw=body.phone,
        specialization_id=body.specialization_id,
    )
    await audit.record(
        actor_id=actor,
        action="clinic_staff.added",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"membership_id": str(result.membership.id)},
    )
    return DoctorCreatedResponse(id=result.membership.id)
