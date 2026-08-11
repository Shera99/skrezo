"""Роут: POST /auth/change-password — смена своего пароля (команда Skrezo)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.config import settings
from app.modules.identity.application.use_cases.change_password import ChangePasswordUseCase
from app.modules.identity.presentation.deps import get_change_password_use_case
from app.modules.identity.presentation.schemas import ChangePasswordRequest, OkResponse
from app.shared.api.rate_limit import ip_rate_limit
from app.shared.auth import ClientContext, get_client_context

router = APIRouter()

_rate_limit = ip_rate_limit(
    scope="change-password",
    limit=settings.change_password_rate_limit,
    window=settings.change_password_rate_window_sec,
)

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Неверный текущий пароль",
        "content": {
            "application/json": {
                "example": {"error": "invalid_credentials", "message": "Неверный текущий пароль"}
            }
        },
    },
    409: {
        "description": "Аккаунт входит по коду из SMS — пароля у него нет",
        "content": {
            "application/json": {
                "example": {
                    "error": "use_otp_login",
                    "message": "Этот аккаунт входит по коду из SMS",
                }
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
    _rl: None = Depends(_rate_limit),
) -> OkResponse:
    """Сменить пароль (в т.ч. обязательную смену временного при первом входе).

    Только для команды Skrezo: у остальных аккаунтов пароля нет, они входят по
    коду из SMS — им ответ 409 ``use_otp_login``.
    """
    await use_case.execute(
        context.account_id,
        current_password=body.current_password,
        new_password=body.new_password,
    )
    return OkResponse()
