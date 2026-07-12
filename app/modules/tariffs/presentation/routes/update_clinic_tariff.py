"""Роут: POST /admin/tariffs/clinic — новая версия тарифа клиники (пакеты)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.tariffs.application.use_cases.update_clinic_tariff import UpdateClinicTariffUseCase
from app.modules.tariffs.domain.pricing import PackageDef
from app.modules.tariffs.presentation.deps import get_update_clinic_use_case
from app.modules.tariffs.presentation.schemas import (
    ClinicTariffCreatedResponse,
    UpdateClinicRequest,
)

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {
        "description": "Некорректные пакеты (границы/цены не строго возрастают и т.п.)",
        "content": {
            "application/json": {
                "example": {"error": "tariff_not_increasing", "message": "..."}
            }
        },
    },
}


@router.post(
    "/admin/tariffs/clinic",
    status_code=status.HTTP_201_CREATED,
    response_model=ClinicTariffCreatedResponse,
    summary="Обновить тариф клиники",
    response_description="Новая версия и число затронутых клиник",
    responses=_ERRORS,
)
async def update_clinic(
    body: UpdateClinicRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: UpdateClinicTariffUseCase = Depends(get_update_clinic_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ClinicTariffCreatedResponse:
    packages = [PackageDef(name=p.name, up_to=p.up_to, price=p.price) for p in body.packages]
    result = await use_case.execute(packages, body.over_price, actor)
    await audit.record(
        actor_id=actor,
        action="tariff.clinic_updated",
        entity_type="tariff_version",
        entity_id=result.version_id,
        payload={"packages": len(body.packages), "version": result.version},
    )
    return ClinicTariffCreatedResponse(
        tariff_version_id=result.version_id,
        version=result.version,
        affected_clients=result.affected_clients,
    )
