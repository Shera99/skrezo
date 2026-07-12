"""Роут: POST /admin/clients/{id}/change-admin-phone — прямая смена номера админа."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clients.application.use_cases.change_admin_phone import ChangeAdminPhoneUseCase
from app.modules.clients.presentation.deps import get_change_admin_phone_use_case
from app.modules.clients.presentation.schemas import (
    ChangeAdminPhoneRequest,
    ChangeAdminPhoneResponse,
)
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Клиент не найден"},
    409: {"description": "Номер уже используется"},
}


@router.post(
    "/admin/clients/{clinic_id}/admin-phone",
    response_model=ChangeAdminPhoneResponse,
    summary="Сменить номер администратора (напрямую)",
    response_description="Установленный номер администратора",
    responses=_ERRORS,
)
async def change_admin_phone(
    clinic_id: uuid.UUID,
    body: ChangeAdminPhoneRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ChangeAdminPhoneUseCase = Depends(get_change_admin_phone_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ChangeAdminPhoneResponse:
    """Команда Skrezo меняет номер владельца клиники одним шагом (без OTP)."""
    phone = await use_case.execute(clinic_id, body.new_phone)
    await audit.record(
        actor_id=actor,
        action="admin_phone.changed",
        entity_type="clinic",
        entity_id=clinic_id,
    )
    return ChangeAdminPhoneResponse(phone=phone)
