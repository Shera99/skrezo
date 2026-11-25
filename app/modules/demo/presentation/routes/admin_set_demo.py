"""Роут: PUT /admin/clients/{clinic_id}/demo — признак демо-клиники (Skrezo)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.demo.application.use_cases.set_demo import SetDemoUseCase
from app.modules.demo.presentation.deps import get_set_demo_use_case
from app.modules.demo.presentation.schemas import DemoStateResponse, SetDemoRequest
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Клиент не найден",
        "content": {"application/json": {"example": {"error": "client_not_found"}}},
    },
    422: {
        "description": "Включение не подтверждено названием клиники",
        "content": {
            "application/json": {
                "example": {
                    "error": "confirm_name_mismatch",
                    "message": "Название клиники не совпадает — признак демо не включён",
                }
            }
        },
    },
}


@router.put(
    "/admin/clients/{clinic_id}/demo",
    response_model=DemoStateResponse,
    summary="Признак демо-клиники",
    response_description="Признак после правки",
    responses=_ERRORS,
)
async def set_demo(
    clinic_id: uuid.UUID,
    body: SetDemoRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: SetDemoUseCase = Depends(get_set_demo_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> DemoStateResponse:
    """Включить или снять признак демо-клиники.

    ⚠️ У демо-клиники каждое утро удаляется всё, что появилось после включения,
    поэтому оно подтверждается названием (`confirmName`). Данные до включения
    сброс не трогает. Плановые напоминания пациентам у демо-клиники не уходят.
    """
    state = await use_case.execute(clinic_id, demo=body.demo, confirm_name=body.confirm_name)
    await audit.record(
        actor_id=actor,
        action="client.demo",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"demo": state.demo},
    )
    return DemoStateResponse.from_state(state)
