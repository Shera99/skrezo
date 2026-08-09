"""Роут: POST /clinic/patients — создать глобального пациента (patients.edit)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.patients.application.use_cases.create_patient import CreatePatientUseCase
from app.modules.patients.presentation.deps import get_create_patient_use_case
from app.modules.patients.presentation.schemas import CreatePatientRequest, PatientCreatedResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {"description": "Телефон уже используется (в ошибке — patientId существующего)"},
}


@router.post(
    "/clinic/patients",
    status_code=status.HTTP_201_CREATED,
    response_model=PatientCreatedResponse,
    summary="Создать пациента (глобального)",
    responses=_ERRORS,
)
async def create_patient(
    body: CreatePatientRequest,
    context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: CreatePatientUseCase = Depends(get_create_patient_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> PatientCreatedResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    patient = await use_case.execute(
        phone=body.phone,
        first_name=body.first_name,
        last_name=body.last_name,
        middle_name=body.middle_name,
        gender=body.gender,
        birth_date=body.birth_date,
        email=body.email,
        note=body.note,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="patient.create",
        entity_type="patient",
        entity_id=patient.id,
        payload=None,
    )
    return PatientCreatedResponse(id=patient.id)
