"""Роут: POST /auth/login — вход команды Skrezo по телефону и паролю.

Единственный контур с паролем. Клиники и соло-врачи входят по OTP
(``/auth/otp/request`` → ``/auth/otp/verify``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis

from app.core.config import settings
from app.core.redis import get_redis
from app.modules.identity.application.use_cases.login import LoginUseCase
from app.modules.identity.presentation.deps import get_login_use_case
from app.modules.identity.presentation.schemas import (
    AccountOut,
    ClientTokenResponse,
    ClinicMembershipOut,
    LoginRequest,
)
from app.shared.api.rate_limit import client_ip, enforce_rate_limit
from app.shared.domain.value_objects import normalize_phone

router = APIRouter()


async def _login_rate_limit(
    request: Request, body: LoginRequest, redis: Redis = Depends(get_redis)
) -> None:
    """Лимит попыток входа на пару IP + нормализованный телефон."""
    try:
        phone = normalize_phone(body.phone)
    except Exception:  # для ключа лимита сгодится и сырой ввод
        phone = body.phone
    await enforce_rate_limit(
        redis,
        f"rl:login:{client_ip(request)}:{phone}",
        settings.login_rate_limit,
        settings.login_rate_window_sec,
    )

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "Неверный телефон или пароль (в т.ч. аккаунт не из команды Skrezo)",
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
    summary="Вход команды Skrezo по телефону и паролю",
    response_description="Токены, аккаунт, список клиник и флаг обязательной смены пароля",
    responses=_ERRORS,
)
async def login(
    body: LoginRequest,
    use_case: LoginUseCase = Depends(get_login_use_case),
    _rate_limit: None = Depends(_login_rate_limit),
) -> ClientTokenResponse:
    """Проверить телефон + пароль и выдать токены сотруднику команды Skrezo.

    Паролем входят **только** аккаунты админ-панели (``isPlatformAdmin``).
    Владельцы клиник, менеджеры, работники и соло-врачи входят по коду:
    ``POST /auth/otp/request`` → ``POST /auth/otp/verify``. Для них этот роут
    отвечает 401 ``invalid_credentials``, как и на несуществующий номер.

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
