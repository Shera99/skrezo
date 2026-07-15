"""Роут: GET /clinic/patients/{id} — карточка пациента (право patients.view)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.get_patient import GetPatientUseCase
from app.modules.patients.presentation.deps import get_get_patient_use_case
from app.modules.patients.presentation.schemas import PatientDetailResponse
from app.shared.auth import ClientContext

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Пациент не найден"}}


@router.get(
    "/clinic/patients/{patient_id}",
    response_model=PatientDetailResponse,
    summary="Карточка пациента (данные + одонтограмма)",
    responses=_NOT_FOUND,
)
async def get_patient(
    patient_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("patients.view")),
    use_case: GetPatientUseCase = Depends(get_get_patient_use_case),
) -> PatientDetailResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    detail = await use_case.execute(context.clinic_id, patient_id)
    return PatientDetailResponse.from_detail(detail)
