"""Роут: PATCH /clinic/services/{id} — правка/деактивация услуги (price.edit)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.services.application.use_cases.update_service import UpdateServiceUseCase
from app.modules.services.presentation.deps import get_update_service_use_case
from app.modules.services.presentation.schemas import ServiceOut, UpdateServiceRequest
from app.shared.auth import ClientContext

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Услуга не найдена"}}


@router.patch(
    "/clinic/services/{service_id}",
    response_model=ServiceOut,
    summary="Правка услуги (в т.ч. деактивация)",
    responses=_NOT_FOUND,
)
async def update_service(
    service_id: uuid.UUID,
    body: UpdateServiceRequest,
    context: ClientContext = Depends(require_permission("price.edit")),
    use_case: UpdateServiceUseCase = Depends(get_update_service_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ServiceOut:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    service = await use_case.execute(
        context.clinic_id, service_id, body.model_dump(exclude_unset=True)
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="service.update",
        entity_type="service",
        entity_id=service_id,
        payload={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    return ServiceOut.from_orm_service(service)
