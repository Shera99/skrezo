"""Роут: GET /clinic/patients/{id} — карточка + одонтограмма (patients.view + связь)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.get_patient import GetPatientUseCase
from app.modules.patients.presentation.deps import get_get_patient_use_case
from app.modules.patients.presentation.schemas import PatientCardResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": (
            "Пациент не найден ЛИБО у клиники нет с ним приёмов — ответ один и тот "
            "же: существование чужих пациентов не подтверждаем"
        ),
        "content": {
            "application/json": {
                "example": {"error": "patient_not_found", "message": "Пациент не найден"}
            }
        },
    },
}


@router.get(
    "/clinic/patients/{patient_id}",
    response_model=PatientCardResponse,
    summary="Карточка пациента (одонтограмма)",
    responses=_ERRORS,
)
async def get_patient(
    patient_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("patients.view")),
    use_case: GetPatientUseCase = Depends(get_get_patient_use_case),
) -> PatientCardResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    card = await use_case.execute(context.clinic_id, patient_id)
    return PatientCardResponse.from_card(card)
