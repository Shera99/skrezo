"""Роут: POST /auth/otp/verify — подтвердить код (шаг 2): вход или анкета."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.core.config import settings
from app.modules.identity.application.use_cases.verify_otp import VerifyOtpUseCase
from app.modules.identity.presentation.deps import get_verify_otp_use_case
from app.modules.identity.presentation.schemas import (
    ClientTokenResponse,
    OtpVerifyRequest,
    OtpVerifyResponse,
)
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
    409: {
        "description": "Номер входит по паролю (сотрудник клиники)",
        "content": {
            "application/json": {
                "example": {
                    "error": "use_password_login",
                    "message": "Этот номер входит по паролю",
                }
            }
        },
    },
}


@router.post(
    "/otp/verify",
    response_model=OtpVerifyResponse,
    summary="Подтвердить OTP-код",
    response_description="Вход (auth) либо билет на регистрацию (registrationToken)",
    responses=_ERRORS,
    dependencies=[
        Depends(
            ip_rate_limit(
                scope="otp-verify",
                limit=settings.otp_request_rate_limit,
                window=settings.otp_request_rate_window_sec,
            )
        )
    ],
)
async def verify_otp(
    body: OtpVerifyRequest,
    use_case: VerifyOtpUseCase = Depends(get_verify_otp_use_case),
) -> OtpVerifyResponse:
    """Шаг 2: проверить код и развести три случая.

    * аккаунта нет → ``registration_required`` + ``registrationToken`` (клиент
      показывает анкету и вызывает ``POST /auth/solo/register``);
    * соло-аккаунт (без пароля) → ``authenticated`` + токены в ``auth``;
    * номер сотрудника клиники (с паролем) → 409 ``use_password_login``.
    """
    result = await use_case.execute(body.phone, body.code)
    return OtpVerifyResponse(
        status=result.status,
        registration_token=result.registration_token,
        auth=(
            ClientTokenResponse.from_login_result(result.login)
            if result.login is not None
            else None
        ),
    )
