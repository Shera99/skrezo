"""Роут: GET /clinic/patients/{id}/teeth/{fdi} — сквозная история зуба (view_medical)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.tooth_history import ToothHistoryUseCase
from app.modules.patients.presentation.deps import get_tooth_history_use_case
from app.modules.patients.presentation.schemas import ToothHistoryItem, ToothHistoryResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Пациент не найден либо у клиники нет с ним приёмов"},
    422: {"description": "Некорректный номер зуба (FDI)"},
}


@router.get(
    "/clinic/patients/{patient_id}/teeth/{tooth}",
    response_model=ToothHistoryResponse,
    summary="История зуба (сквозная по всем клиникам)",
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
        tooth=tooth, records=[ToothHistoryItem.from_entry(e) for e in records]
    )
