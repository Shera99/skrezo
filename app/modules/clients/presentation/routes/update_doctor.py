"""Роут: PATCH /admin/clients/{id}/doctors/{docId} — правка врача (админ-сайд)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import NotFoundError
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository
from app.modules.clients.presentation.deps import get_client_repository
from app.modules.clients.presentation.schemas import DoctorUpdateRequest, StaffMember
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.staff.application.use_cases.update_staff import UpdateStaffUseCase
from app.modules.staff.presentation.deps import get_update_staff_use_case
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Владельца нельзя редактировать здесь (cannot_edit_owner)"},
    404: {"description": "Сотрудник не найден"},
    409: {"description": "Телефон уже используется"},
}


@router.patch(
    "/admin/clients/{clinic_id}/doctors/{doctor_id}",
    response_model=StaffMember,
    summary="Правка врача (админ-сайд)",
    response_description="Обновлённый врач",
    responses=_ERRORS,
)
async def update_doctor(
    clinic_id: uuid.UUID,
    doctor_id: uuid.UUID,
    body: DoctorUpdateRequest,
    lang: Lang = Depends(get_request_lang),
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: UpdateStaffUseCase = Depends(get_update_staff_use_case),
    clients: ClientRepository = Depends(get_client_repository),
    audit: AuditService = Depends(get_audit_service),
) -> StaffMember:
    await use_case.execute(
        clinic_id,
        doctor_id,
        full_name=body.full_name,
        phone_raw=body.phone,
        specialization_id=body.specialization_id,
    )
    await audit.record(
        actor_id=actor,
        action="clinic_staff.updated",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"membership_id": str(doctor_id)},
    )
    # Резолвим название специализации для ответа (спека StaffMember).
    row = await clients.staff_member(clinic_id, doctor_id, lang)
    if row is None:  # практически недостижимо после успешного апдейта
        raise NotFoundError("Сотрудник не найден", code="staff_not_found")
    return StaffMember.from_row(row)
