"""Роут: PUT /clinic/patients/{id}/teeth — карта зубов upsert (patients.edit + связь)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.patients.application.use_cases.upsert_teeth import UpsertTeethUseCase
from app.modules.patients.domain.models import ToothStatePatch
from app.modules.patients.presentation.deps import get_upsert_teeth_use_case
from app.modules.patients.presentation.schemas import (
    OdontogramResponse,
    OdontogramToothOut,
    UpsertTeethRequest,
)
from app.shared.auth import ClientContext

router = APIRouter()


@router.put(
    "/clinic/patients/{patient_id}/teeth",
    response_model=OdontogramResponse,
    summary="Создать/обновить одонтограмму (карта зубов)",
)
async def upsert_teeth(
    patient_id: uuid.UUID,
    body: UpsertTeethRequest,
    context: ClientContext = Depends(require_permission("patients.edit")),
    use_case: UpsertTeethUseCase = Depends(get_upsert_teeth_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> OdontogramResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    patches = [
        ToothStatePatch(tooth_number=t.tooth, status=t.status, note=t.note) for t in body.teeth
    ]
    teeth = await use_case.execute(context.clinic_id, patient_id, patches)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="patient.teeth",
        entity_type="patient",
        entity_id=patient_id,
        payload={"teeth": [t.tooth_number for t in patches]},
    )
    return OdontogramResponse(odontogram=[OdontogramToothOut.from_orm_tooth(t) for t in teeth])
