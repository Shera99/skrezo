"""Роут: GET /clinic/patients/{id}/visits/{visitId} — детали визита (view_medical)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.get_visit import GetVisitUseCase
from app.modules.patients.presentation.deps import get_get_visit_use_case
from app.modules.patients.presentation.schemas import VisitDetailResponse
from app.shared.auth import ClientContext

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Пациент или визит не найден"},
}


@router.get(
    "/clinic/patients/{patient_id}/visits/{visit_id}",
    response_model=VisitDetailResponse,
    summary="Детали визита (записи по зубам)",
    responses=_NOT_FOUND,
)
async def get_visit(
    patient_id: uuid.UUID,
    visit_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("patients.view_medical")),
    use_case: GetVisitUseCase = Depends(get_get_visit_use_case),
) -> VisitDetailResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    detail = await use_case.execute(context.clinic_id, patient_id, visit_id)
    return VisitDetailResponse.from_detail(detail)
