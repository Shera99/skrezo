"""Роут: POST /auth/otp/request — выпустить OTP-код (шаг 1 входа).

Основной вход платформы: клиники (владелец/менеджер/работник) и соло-врачи.
Пароль остался только у команды Skrezo (``POST /auth/login``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis

from app.core.config import settings
from app.core.redis import get_redis
from app.modules.identity.application.use_cases.request_otp import RequestOtpUseCase
from app.modules.identity.presentation.deps import get_request_otp_use_case
from app.modules.identity.presentation.schemas import OtpRequestRequest, OtpRequestResponse
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


async def _otp_rate_limit(
    request: Request, body: OtpRequestRequest, redis: Redis = Depends(get_redis)
) -> None:
    """Лимит на выпуск кода — по паре IP + нормализованный телефон (SMS дорогие).

    Номер проверяется ДО счётчика: на некорректный номер код не выпускается и
    SMS не уходит — беречь нечего, сразу 422 ``invalid_phone``. Если считать и
    такие попытки, пять опечаток подряд запирают человека на всё окно лимита
    (429), хотя ни одного кода он так и не получил.
    """
    phone = normalize_phone(body.phone)
    await enforce_rate_limit(
        redis,
        f"rl:otp:{client_ip(request)}:{phone}",
        settings.otp_request_rate_limit,
        settings.otp_request_rate_window_sec,
    )


@router.post(
    "/otp/request",
    response_model=OtpRequestResponse,
    summary="Запросить OTP-код",
    response_description="Код выпущен (ответ одинаков вне зависимости от наличия аккаунта)",
    responses=_ERRORS,
)
async def request_otp(
    body: OtpRequestRequest,
    use_case: RequestOtpUseCase = Depends(get_request_otp_use_case),
    _rate_limit: None = Depends(_otp_rate_limit),
) -> OtpRequestResponse:
    """Шаг 1: выпустить 6-значный код и отправить на телефон.

    Ответ не раскрывает, зарегистрирован ли номер. Код уходит по каналам из
    ``OTP_CHANNELS`` в порядке приоритета (Telegram → WhatsApp → SMS): первый
    сработавший останавливает цепочку. Если не справился ни один — 502
    ``otp_delivery_failed``. Каналы не настроены — код пишется в лог, а при
    ``OTP_EXPOSE_CODE`` (dev) возвращается в поле ``devCode``.
    """
    code = await use_case.execute(body.phone)
    return OtpRequestResponse(
        sent=True, dev_code=code if settings.otp_expose_code else None
    )
