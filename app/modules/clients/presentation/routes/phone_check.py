"""Роут: GET /admin/clients/phone-check — свободен ли номер (для формы создания)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.modules.clients.application.use_cases.phone_check import PhoneCheckUseCase
from app.modules.clients.presentation.deps import get_phone_check_use_case
from app.modules.clients.presentation.schemas import PhoneCheckResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()


@router.get(
    "/admin/clients/phone-check",
    response_model=PhoneCheckResponse,
    summary="Проверка телефона",
    response_description="ФИО владельца (занят) или null (свободен)",
)
async def phone_check(
    phone: str = Query(description="Номер в любом формате"),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: PhoneCheckUseCase = Depends(get_phone_check_use_case),
) -> PhoneCheckResponse:
    owner_name = await use_case.execute(phone)
    return PhoneCheckResponse(owner_name=owner_name)
