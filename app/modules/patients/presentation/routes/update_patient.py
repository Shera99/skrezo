"""Роут: PATCH /clinic/patients/{id} — правка пациента (право patients.edit)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.update_patient import UpdatePatientUseCase
from app.modules.patients.presentation.deps import get_update_patient_use_case
from app.modules.patients.presentation.schemas import PatientDetailResponse, UpdatePatientRequest
from app.shared.auth import ClientContext

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Пациент не найден"}}


@router.patch(
    "/clinic/patients/{patient_id}",
    response_model=PatientDetailResponse,
    summary="Правка карточки пациента",
    responses=_NOT_FOUND,
)
async def update_patient(
    patient_id: uuid.UUID,
    body: UpdatePatientRequest,
    context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: UpdatePatientUseCase = Depends(get_update_patient_use_case),
) -> PatientDetailResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    detail = await use_case.execute(
        context.clinic_id, patient_id, body.model_dump(exclude_unset=True)
    )
    return PatientDetailResponse.from_detail(detail)
