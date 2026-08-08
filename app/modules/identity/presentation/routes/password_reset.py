"""Роут: POST /auth/password/reset — новый пароль по коду из сообщения (шаг 2)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.config import settings
from app.modules.identity.application.use_cases.reset_password import ResetPasswordUseCase
from app.modules.identity.presentation.deps import get_reset_password_use_case
from app.modules.identity.presentation.schemas import OkResponse, ResetPasswordRequest
from app.shared.api.rate_limit import ip_rate_limit

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {
        "description": "Неверный или истёкший код",
        "content": {
            "application/json": {
                "example": {"error": "invalid_otp", "message": "Неверный или истёкший код"}
            }
        },
    },
    404: {
        "description": "Аккаунта с таким номером нет",
        "content": {
            "application/json": {
                "example": {"error": "account_not_found", "message": "Аккаунт не найден"}
            }
        },
    },
    409: {
        "description": "Номер входит по коду из SMS (соло-врач) — пароля у него нет",
        "content": {
            "application/json": {
                "example": {
                    "error": "use_otp_login",
                    "message": "Этот номер входит по коду из SMS",
                }
            }
        },
    },
    429: {
        "description": "Слишком много неверных попыток — код сожжён, нужен новый",
        "content": {
            "application/json": {
                "example": {
                    "error": "otp_too_many_attempts",
                    "message": "Слишком много попыток, запросите код заново",
                }
            }
        },
    },
}


@router.post(
    "/password/reset",
    response_model=OkResponse,
    summary="Сменить забытый пароль по коду",
    response_description="Пароль изменён; прежние сессии отозваны",
    responses=_ERRORS,
    dependencies=[
        Depends(
            ip_rate_limit(
                scope="password-reset",
                limit=settings.otp_request_rate_limit,
                window=settings.otp_request_rate_window_sec,
            )
        )
    ],
)
async def reset_password(
    body: ResetPasswordRequest,
    use_case: ResetPasswordUseCase = Depends(get_reset_password_use_case),
) -> OkResponse:
    """Шаг 2: проверить код из ``/auth/password/forgot`` и поставить новый пароль.

    После успеха все refresh-сессии аккаунта отозваны — входить нужно заново, уже
    новым паролём. Ранее выданные access-токены доживают свой короткий срок.
    """
    await use_case.execute(body.phone, code=body.code, new_password=body.new_password)
    return OkResponse()
