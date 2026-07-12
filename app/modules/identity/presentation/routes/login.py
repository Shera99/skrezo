"""Роут: POST /auth/login — вход сотрудника клиники по телефону и паролю."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.modules.identity.application.use_cases.login import LoginUseCase
from app.modules.identity.presentation.deps import get_login_use_case
from app.modules.identity.presentation.schemas import (
    AccountOut,
    ClientTokenResponse,
    ClinicMembershipOut,
    LoginRequest,
)

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Неверный телефон или пароль",
        "content": {
            "application/json": {
                "example": {
                    "error": "invalid_credentials",
                    "message": "Неверный телефон или пароль",
                }
            }
        },
    },
}


@router.post(
    "/login",
    response_model=ClientTokenResponse,
    summary="Вход по телефону и паролю",
    response_description="Токены, аккаунт, список клиник и флаг обязательной смены пароля",
    responses=_ERRORS,
)
async def login(
    body: LoginRequest,
    use_case: LoginUseCase = Depends(get_login_use_case),
) -> ClientTokenResponse:
    """Проверить телефон + пароль и выдать клиентские токены.

    Если ``mustChangePassword = true`` — пароль временный, клиент обязан вызвать
    ``POST /auth/change-password`` перед дальнейшей работой.
    """
    result = await use_case.execute(body.phone, body.password)
    return ClientTokenResponse(
        access_token=result.tokens.access_token,
        refresh_token=result.tokens.refresh_token,
        expires_in=result.tokens.expires_in,
        account=AccountOut.model_validate(result.account),
        clinics=[ClinicMembershipOut.from_context(m) for m in result.memberships],
        active_clinic_id=result.active_clinic_id,
        must_change_password=result.must_change_password,
        is_platform_admin=result.is_platform_admin,
    )
