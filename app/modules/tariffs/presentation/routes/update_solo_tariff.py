"""Роут: POST /admin/tariffs/solo — новая версия тарифа соло."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.tariffs.application.use_cases.update_solo_tariff import UpdateSoloTariffUseCase
from app.modules.tariffs.presentation.deps import get_update_solo_use_case
from app.modules.tariffs.presentation.schemas import SoloTariffCreatedResponse, UpdateSoloRequest

router = APIRouter()


@router.post(
    "/admin/tariffs/solo",
    status_code=status.HTTP_201_CREATED,
    response_model=SoloTariffCreatedResponse,
    summary="Обновить тариф соло",
    response_description="Идентификатор и номер новой версии тарифа",
)
async def update_solo(
    body: UpdateSoloRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: UpdateSoloTariffUseCase = Depends(get_update_solo_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> SoloTariffCreatedResponse:
    result = await use_case.execute(body.price, actor)
    await audit.record(
        actor_id=actor,
        action="tariff.solo_updated",
        entity_type="tariff_version",
        entity_id=result.version_id,
        payload={"price": body.price, "version": result.version},
    )
    return SoloTariffCreatedResponse(tariff_version_id=result.version_id, version=result.version)
