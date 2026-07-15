"""Роут: POST /clinic/patients/{id}/visits — создать визит (право patients.edit)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.create_visit import CreateVisitUseCase, VisitInput
from app.modules.patients.presentation.deps import get_create_visit_use_case
from app.modules.patients.presentation.schemas import CreateVisitRequest, VisitCreatedResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Пациент не найден"},
    422: {"description": "Некорректный зуб (FDI) или врач не из клиники"},
}


@router.post(
    "/clinic/patients/{patient_id}/visits",
    status_code=status.HTTP_201_CREATED,
    response_model=VisitCreatedResponse,
    summary="Создать визит с манипуляциями по зубам",
    responses=_ERRORS,
)
async def create_visit(
    patient_id: uuid.UUID,
    body: CreateVisitRequest,
    context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: CreateVisitUseCase = Depends(get_create_visit_use_case),
) -> VisitCreatedResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    data = VisitInput(
        doctor_membership_id=body.doctor_membership_id,
        visited_at=body.visited_at,
        complaint=body.complaint,
        note=body.note,
        records=[r.model_dump() for r in body.records],
    )
    visit = await use_case.execute(context.clinic_id, patient_id, data)
    return VisitCreatedResponse(id=visit.id)
