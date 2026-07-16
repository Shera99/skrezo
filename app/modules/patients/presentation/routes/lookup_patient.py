"""Роут: GET /clinic/patients/lookup — глобальный поиск по телефону (patients.edit)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.lookup_patient import LookupPatientUseCase
from app.modules.patients.presentation.deps import get_lookup_patient_use_case
from app.modules.patients.presentation.schemas import PatientIdentity, PatientLookupResponse
from app.shared.auth import ClientContext

router = APIRouter()


@router.get(
    "/clinic/patients/lookup",
    response_model=PatientLookupResponse,
    summary="Поиск пациента по телефону (для привязки к приёму)",
)
async def lookup_patient(
    phone: str = Query(description="Телефон в любом формате"),
    _context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: LookupPatientUseCase = Depends(get_lookup_patient_use_case),
) -> PatientLookupResponse:
    patient = await use_case.execute(phone)
    return PatientLookupResponse(
        patient=PatientIdentity.from_patient(patient) if patient else None
    )
