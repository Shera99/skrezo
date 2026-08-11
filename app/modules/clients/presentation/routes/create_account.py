"""Роут: POST /admin/clients — создать аккаунт клиента (клиника/соло)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clients.application.use_cases.create_account import CreateAccountUseCase
from app.modules.clients.presentation.deps import get_create_account_use_case
from app.modules.clients.presentation.schemas import AccountCreatedResponse, CreateAccountRequest
from app.modules.clinics.application.services.provisioning_service import ProvisionCommand
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    409: {
        "description": "Телефон уже используется (в сообщении — имя владельца)",
        "content": {
            "application/json": {"example": {"error": "phone_already_used", "message": "..."}}
        },
    },
}


@router.post(
    "/admin/clients",
    status_code=status.HTTP_201_CREATED,
    response_model=AccountCreatedResponse,
    summary="Создать аккаунт клиента",
    response_description="Идентификатор, тип и телефон владельца",
    responses=_ERRORS,
)
async def create_account(
    body: CreateAccountRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: CreateAccountUseCase = Depends(get_create_account_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> AccountCreatedResponse:
    """Атомарно создаёт клинику/соло + владельца + роли + подписку (триал 30 дней).

    Пароль владельцу не выдаётся: он входит по коду подтверждения на свой номер
    (``POST /auth/otp/request`` → ``POST /auth/otp/verify``). Сообщение об этом
    уходит владельцу автоматически.
    """
    command = ProvisionCommand(
        type=body.type,
        admin_full_name=body.admin_full_name,
        admin_phone=body.admin_phone,
        city_id=body.city_id,
        clinic_name=body.name,
        address=body.address,
        business_phone=body.business_phone,
        seats_limit=body.seats,
        lang=body.language,
    )
    result = await use_case.execute(command)
    await audit.record(
        actor_id=actor,
        action="clinic.created",
        entity_type="clinic",
        entity_id=result.clinic.id,
        payload={"type": result.clinic.type.value},
    )
    return AccountCreatedResponse(
        id=result.clinic.id,
        type=result.clinic.type,
        admin_phone=result.account.phone,
    )
