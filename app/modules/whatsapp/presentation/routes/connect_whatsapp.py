"""Роут: PUT /admin/clients/{clinic_id}/whatsapp — привязать номер (Skrezo)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.whatsapp.application.use_cases.connect_whatsapp import ConnectWhatsAppUseCase
from app.modules.whatsapp.presentation.deps import get_connect_whatsapp_use_case
from app.modules.whatsapp.presentation.schemas import (
    ClinicWhatsAppResponse,
    ConnectWhatsAppRequest,
)

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Клиент не найден"},
    409: {
        "description": "Номер уже привязан к другой клинике",
        "content": {
            "application/json": {
                "example": {
                    "error": "phone_number_taken",
                    "message": "Этот номер уже привязан к другой клинике",
                }
            }
        },
    },
}


@router.put(
    "/admin/clients/{clinic_id}/whatsapp",
    response_model=ClinicWhatsAppResponse,
    summary="Привязать номер WhatsApp к клинике",
    response_description="Привязка после сохранения",
    responses=_ERRORS,
)
async def connect_whatsapp(
    clinic_id: uuid.UUID,
    body: ConnectWhatsAppRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ConnectWhatsAppUseCase = Depends(get_connect_whatsapp_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ClinicWhatsAppResponse:
    """Завести или обновить привязку номера Cloud API (одна на клинику).

    `phoneNumberId` — идентификатор номера в кабинете Meta; по нему вебхук
    находит клинику. Секреты (access token, APP_SECRET) сюда не входят — они
    живут в переменных окружения сервиса. `active: false` выключает приём
    вебхука, сохраняя историю переписки.
    """
    binding = await use_case.execute(
        clinic_id,
        phone_number_id=body.phone_number_id,
        display_phone=body.display_phone,
        waba_id=body.waba_id,
        mode=body.mode,
        active=body.active,
    )
    await audit.record(
        actor_id=actor,
        action="client.whatsapp_connected",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"phoneNumberId": body.phone_number_id, "active": body.active},
    )
    return ClinicWhatsAppResponse.from_binding(binding)
