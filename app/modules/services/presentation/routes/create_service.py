"""Роут: POST /clinic/services — создать услугу (price.edit)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.services.application.use_cases.create_service import CreateServiceUseCase
from app.modules.services.presentation.deps import get_create_service_use_case
from app.modules.services.presentation.schemas import CreateServiceRequest, ServiceOut
from app.shared.auth import ClientContext

router = APIRouter()


@router.post(
    "/clinic/services",
    status_code=status.HTTP_201_CREATED,
    response_model=ServiceOut,
    summary="Создать услугу",
)
async def create_service(
    body: CreateServiceRequest,
    context: ClientContext = Depends(require_permission("price.edit")),
    use_case: CreateServiceUseCase = Depends(get_create_service_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ServiceOut:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    service = await use_case.execute(
        context.clinic_id,
        name=body.name,
        description=body.description,
        price_tiyin=body.price_tiyin,
        duration=body.duration,
        tooth_outcome=body.tooth_outcome,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="service.create",
        entity_type="service",
        entity_id=service.id,
        payload={"name": service.name, "priceTiyin": service.price_tiyin},
    )
    return ServiceOut.from_orm_service(service)
