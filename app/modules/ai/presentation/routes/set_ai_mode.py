"""Роут: PUT /admin/clients/{clinic_id}/ai-mode — режим ассистента (Skrezo).

Режим — уровень клиники (§13.1): новая подключается только в shadow, переход
в auto — решение по цифрам теневого режима, поэтому ручка платформенная, а не
клиентская.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFoundError
from app.modules.ai.presentation.schemas import AiModeResponse, SetAiModeRequest
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.db.models.clinic import Clinic

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Клиент не найден"}}


@router.put(
    "/admin/clients/{clinic_id}/ai-mode",
    response_model=AiModeResponse,
    summary="Режим AI-ассистента клиники",
    response_description="Режим после смены",
    responses=_NOT_FOUND,
)
async def set_ai_mode(
    clinic_id: uuid.UUID,
    body: SetAiModeRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    session: AsyncSession = Depends(get_session),
    audit: AuditService = Depends(get_audit_service),
) -> AiModeResponse:
    clinic = await session.get(Clinic, clinic_id)
    if clinic is None:
        raise NotFoundError("Клиент не найден", code="client_not_found")
    clinic.ai_mode = body.mode
    await session.commit()
    await audit.record(
        actor_id=actor,
        action="client.ai_mode",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"mode": body.mode.value},
    )
    return AiModeResponse(clinic_id=clinic_id, mode=clinic.ai_mode)
