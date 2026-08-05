"""Роут: POST /clinic/patients — создать глобального пациента (patients.edit)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status

from app.modules.access.presentation.deps import require_permission
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
    _context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: CreatePatientUseCase = Depends(get_create_patient_use_case),
) -> PatientCreatedResponse:
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
    return PatientCreatedResponse(id=patient.id)
