"""Роут: POST /auth/change-password — смена пароля текущим аккаунтом."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.modules.identity.application.use_cases.change_password import ChangePasswordUseCase
from app.modules.identity.presentation.deps import get_change_password_use_case
from app.modules.identity.presentation.schemas import ChangePasswordRequest, OkResponse
from app.shared.auth import ClientContext, get_client_context

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Неверный текущий пароль",
        "content": {
            "application/json": {
                "example": {"error": "invalid_credentials", "message": "Неверный текущий пароль"}
            }
        },
    },
}


@router.post(
    "/change-password",
    response_model=OkResponse,
    summary="Сменить пароль",
    response_description="Подтверждение смены",
    responses=_ERRORS,
)
async def change_password(
    body: ChangePasswordRequest,
    context: ClientContext = Depends(get_client_context),
    use_case: ChangePasswordUseCase = Depends(get_change_password_use_case),
) -> OkResponse:
    """Сменить пароль (в т.ч. обязательную смену временного при первом входе)."""
    await use_case.execute(
        context.account_id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return OkResponse()
