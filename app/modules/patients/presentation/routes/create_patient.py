"""Роут: POST /clinic/patients — создать пациента (право patients.edit)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.create_patient import CreatePatientUseCase
from app.modules.patients.presentation.deps import get_create_patient_use_case
from app.modules.patients.presentation.schemas import CreatePatientRequest, PatientCreatedResponse
from app.shared.auth import ClientContext

router = APIRouter()


@router.post(
    "/clinic/patients",
    status_code=status.HTTP_201_CREATED,
    response_model=PatientCreatedResponse,
    summary="Создать пациента",
    response_description="Идентификатор пациента",
)
async def create_patient(
    body: CreatePatientRequest,
    context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: CreatePatientUseCase = Depends(get_create_patient_use_case),
) -> PatientCreatedResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    patient = await use_case.execute(
        context.clinic_id,
        full_name=body.full_name,
        phone=body.phone,
        birth_date=body.birth_date,
        gender=body.gender,
        note=body.note,
    )
    return PatientCreatedResponse(id=patient.id)
