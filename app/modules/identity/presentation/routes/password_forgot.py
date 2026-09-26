"""Роут: POST /auth/password/forgot — код на смену забытого пароля (шаг 1).

Пароль есть только у команды Skrezo, так что и восстановление — их путь.
Остальные входят по коду и забывать им нечего.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis

from app.core.config import settings
from app.core.redis import get_redis
from app.modules.identity.application.use_cases.request_otp import RequestOtpUseCase
from app.modules.identity.domain.models import OtpPurpose
from app.modules.identity.presentation.deps import get_request_otp_use_case
from app.modules.identity.presentation.schemas import ForgotPasswordRequest, OtpRequestResponse
from app.shared.api.rate_limit import client_ip, enforce_rate_limit
from app.shared.domain.value_objects import normalize_phone

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {
        "description": "Номер не распознан как телефон; попытка лимита не расходуется",
        "content": {
            "application/json": {
                "example": {"error": "invalid_phone", "message": "Некорректный номер телефона"}
            }
        },
    },
    429: {
        "description": "Исчерпан лимит выпуска кода на пару IP + телефон; "
        "``retryAfter`` — секунд до сброса",
        "content": {
            "application/json": {
                "example": {
                    "error": "too_many_requests",
                    "message": "Слишком много попыток, попробуйте позже",
                    "retryAfter": 240,
                }
            }
        },
    },
    502: {
        "description": "Код не удалось доставить ни одним каналом",
        "content": {
            "application/json": {
                "example": {
                    "error": "otp_delivery_failed",
                    "message": "Не удалось отправить код, попробуйте ещё раз",
                }
            }
        },
    },
}


async def _forgot_rate_limit(
    request: Request, body: ForgotPasswordRequest, redis: Redis = Depends(get_redis)
) -> None:
    """Лимит на выпуск кода — по паре IP + нормализованный телефон (SMS дорогие).

    Счётчик свой, не общий с ``/auth/otp/request``: иначе запрос кода на
    восстановление тратил бы попытки входа, и наоборот. Некорректный номер,
    как и там, отсекается 422 ``invalid_phone`` до счётчика: SMS на него не
    уходит, а опечатки не должны запирать на всё окно лимита.
    """
    phone = normalize_phone(body.phone)
    await enforce_rate_limit(
        redis,
        f"rl:otp-forgot:{client_ip(request)}:{phone}",
        settings.otp_request_rate_limit,
        settings.otp_request_rate_window_sec,
    )


@router.post(
    "/password/forgot",
    response_model=OtpRequestResponse,
    summary="Запросить код на смену забытого пароля",
    response_description="Код выпущен (ответ одинаков вне зависимости от наличия аккаунта)",
    responses=_ERRORS,
)
async def forgot_password(
    body: ForgotPasswordRequest,
    use_case: RequestOtpUseCase = Depends(get_request_otp_use_case),
    _rate_limit: None = Depends(_forgot_rate_limit),
) -> OtpRequestResponse:
    """Шаг 1: выслать код подтверждения на телефон аккаунта команды Skrezo.

    Дальше — ``POST /auth/password/reset`` с этим кодом и новым паролём. Код
    годится **только** на смену пароля: войти им нельзя, как и сменить пароль
    кодом, выпущенным на вход.

    Ответ не раскрывает, зарегистрирован ли номер: он одинаков и для чужого
    номера. Каналы и dev-режим — те же, что у ``/auth/otp/request``.
    """
    code = await use_case.execute(body.phone, OtpPurpose.PASSWORD_RESET)
    return OtpRequestResponse(sent=True, dev_code=code if settings.otp_expose_code else None)
