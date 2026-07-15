"""Роут: POST /admin/clients/{id}/reset-password — сброс пароля админа клиента."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clients.application.use_cases.reset_admin_password import (
    ResetAdminPasswordUseCase,
)
from app.modules.clients.presentation.deps import get_reset_admin_password_use_case
from app.modules.clients.presentation.schemas import PasswordResetResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {404: {"description": "Клиент не найден"}}


@router.post(
    "/admin/clients/{clinic_id}/reset-password",
    response_model=PasswordResetResponse,
    summary="Сбросить пароль администратора клиента",
    response_description="Новый временный пароль владельца",
    responses=_ERRORS,
)
async def reset_admin_password(
    clinic_id: uuid.UUID,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ResetAdminPasswordUseCase = Depends(get_reset_admin_password_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> PasswordResetResponse:
    temp_password = await use_case.execute(clinic_id)
    await audit.record(
        actor_id=actor,
        action="admin_password.reset",
        entity_type="clinic",
        entity_id=clinic_id,
    )
    return PasswordResetResponse(temp_password=temp_password)
