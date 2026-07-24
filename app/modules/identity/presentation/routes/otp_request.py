"""Роут: POST /auth/otp/request — выпустить OTP-код (шаг 1 соло-входа)."""

from __future__ import annotations

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


async def _otp_rate_limit(
    request: Request, body: OtpRequestRequest, redis: Redis = Depends(get_redis)
) -> None:
    """Лимит на выпуск кода — по паре IP + нормализованный телефон (SMS дорогие)."""
    try:
        phone = normalize_phone(body.phone)
    except Exception:
        phone = body.phone
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
)
async def request_otp(
    body: OtpRequestRequest,
    use_case: RequestOtpUseCase = Depends(get_request_otp_use_case),
    _rate_limit: None = Depends(_otp_rate_limit),
) -> OtpRequestResponse:
    """Шаг 1: выпустить 6-значный код и отправить на телефон.

    Ответ не раскрывает, зарегистрирован ли номер. Канал доставки (SMS/WhatsApp)
    пока не подключён: код уходит в лог, а при ``OTP_EXPOSE_CODE`` (dev) —
    возвращается в поле ``devCode``.
    """
    code = await use_case.execute(body.phone)
    return OtpRequestResponse(
        sent=True, dev_code=code if settings.otp_expose_code else None
    )
