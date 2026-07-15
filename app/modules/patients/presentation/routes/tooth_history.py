"""Роут: GET /clinic/patients/{id}/teeth/{tooth} — история зуба (patients.view_medical)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.tooth_history import ToothHistoryUseCase
from app.modules.patients.presentation.deps import get_tooth_history_use_case
from app.modules.patients.presentation.schemas import ToothHistoryResponse, ToothRecordOut
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Пациент не найден"},
    422: {"description": "Некорректный номер зуба (FDI)"},
}


@router.get(
    "/clinic/patients/{patient_id}/teeth/{tooth}",
    response_model=ToothHistoryResponse,
    summary="История записей по зубу",
    responses=_ERRORS,
)
async def tooth_history(
    patient_id: uuid.UUID,
    tooth: int,
    context: ClientContext = Depends(require_permission("patients.view_medical")),
    use_case: ToothHistoryUseCase = Depends(get_tooth_history_use_case),
) -> ToothHistoryResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    records = await use_case.execute(context.clinic_id, patient_id, tooth)
    return ToothHistoryResponse(
        tooth=tooth, records=[ToothRecordOut.from_orm_record(r) for r in records]
    )
