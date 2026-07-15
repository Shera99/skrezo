"""Роут: GET /clinic/patients/{id}/visits — визиты пациента (patients.view_medical)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.list_visits import ListVisitsUseCase
from app.modules.patients.presentation.deps import get_list_visits_use_case
from app.modules.patients.presentation.schemas import VisitListItem
from app.shared.auth import ClientContext

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Пациент не найден"}}


@router.get(
    "/clinic/patients/{patient_id}/visits",
    response_model=list[VisitListItem],
    summary="Визиты пациента",
    responses=_NOT_FOUND,
)
async def list_visits(
    patient_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("patients.view_medical")),
    use_case: ListVisitsUseCase = Depends(get_list_visits_use_case),
) -> list[VisitListItem]:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    rows = await use_case.execute(context.clinic_id, patient_id)
    return [VisitListItem.from_row(r) for r in rows]
