"""Единый формат ошибок и их обработчики.

Тело ошибки — «плоский конверт» из спецификации:

    { "error": "<машиночитаемый_код>", "message": "...", ...extra }

Прикладной код бросает наследников :class:`AppError`; HTTP-детали (статус,
код) инкапсулированы в исключении, роутеры их не знают.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


class AppError(Exception):
    """Базовая доменная/прикладная ошибка с HTTP-семантикой."""

    code: str = "internal_error"
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    message: str = "Внутренняя ошибка"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.extra = extra or {}
        #: HTTP-заголовки ответа (например, ``Retry-After`` у 429).
        self.headers = headers or {}
        super().__init__(self.message)

    def to_body(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, **self.extra}


class NotFoundError(AppError):
    code = "not_found"
    status_code = status.HTTP_404_NOT_FOUND
    message = "Не найдено"


class ConflictError(AppError):
    code = "conflict"
    status_code = status.HTTP_409_CONFLICT
    message = "Конфликт"


class ValidationAppError(AppError):
    code = "validation_error"
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    message = "Ошибка валидации"


class BadRequestError(AppError):
    code = "bad_request"
    status_code = status.HTTP_400_BAD_REQUEST
    message = "Некорректный запрос"


class UnauthorizedError(AppError):
    code = "unauthorized"
    status_code = status.HTTP_401_UNAUTHORIZED
    message = "Требуется авторизация"


class ForbiddenError(AppError):
    code = "forbidden"
    status_code = status.HTTP_403_FORBIDDEN
    message = "Доступ запрещён"


class PaymentRequiredError(AppError):
    code = "payment_failed"
    status_code = status.HTTP_402_PAYMENT_REQUIRED
    message = "Оплата не прошла"


class TooManyRequestsError(AppError):
    code = "too_many_requests"
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    message = "Слишком много запросов"


class ServiceUnavailableError(AppError):
    """Временная недоступность своей зависимости (модель не ответила, выключена).

    503, а не 502: клиенту это сигнал «повторите позже», и так же его читают
    прокси и мобильные клиенты.
    """

    code = "service_unavailable"
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    message = "Сервис временно недоступен, попробуйте позже"


class UpstreamError(AppError):
    """Отказал внешний провайдер (доставка OTP, платёжный шлюз).

    502, а не 500: у нас всё работает, не ответила чужая система. Клиенту это
    повод повторить попытку, а не сообщать об ошибке в приложении.
    """

    code = "upstream_error"
    status_code = status.HTTP_502_BAD_GATEWAY
    message = "Внешний сервис недоступен, попробуйте ещё раз"


def register_exception_handlers(app: FastAPI) -> None:
    """Подключить обработчики ошибок к приложению."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content=exc.to_body(), headers=exc.headers or None
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={
                "error": "validation_error",
                "message": "Тело запроса не прошло валидацию",
                "details": jsonable_encoder(exc.errors()),
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "message": "Внутренняя ошибка сервера"},
        )
