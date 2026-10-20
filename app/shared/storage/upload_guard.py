"""Отсев загрузок файлов пациента ДО разбора тела запроса.

FastAPI разбирает multipart-форму раньше зависимостей: ``request.form()``
вызывается до ``require_permission``. Для ручки загрузки это значит, что тело
целиком — до гигабайта КТ — ложится во временный каталог ещё до того, как
выяснится, что токена нет вовсе. Анонимный клиент с десятком параллельных
загрузок забивает диск сервера, а на том же диске живёт Postgres.

Фильтр стоит перед приложением и для ``POST …/clinic/patients/<id>/files``
до чтения тела проверяет то, чему не нужна БД:

* ``Content-Length`` не больше лимита КТ (+ запас на multipart) — иначе 413;
* access-токен с живой подписью и сроком — иначе 401.

Полная проверка (блокировка аккаунта, право, членство) по-прежнему идёт в
зависимостях ручки: фильтр лишь не пускает тех, кто заведомо её не пройдёт.
"""

from __future__ import annotations

import re

import jwt
from fastapi import status
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.errors import AppError, UnauthorizedError
from app.core.security import decode_token

#: Ручка загрузки. Поиск с конца пути — префикс (``/api/v1``, ``ROOT_PATH``) любой.
_UPLOAD_PATH = re.compile(r"/clinic/patients/[^/]+/files/?$")

#: Запас на multipart-обвязку и текстовые поля формы поверх самого файла.
_MULTIPART_MARGIN = 1024 * 1024


class UploadGuardMiddleware:
    """ASGI-фильтр: отвечает на заведомо отвергнутую загрузку, не читая тело."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not _UPLOAD_PATH.search(scope["path"])
        ):
            await self.app(scope, receive, send)
            return
        error = reject_upload(Headers(scope=scope))
        if error is None:
            await self.app(scope, receive, send)
            return
        response = JSONResponse(status_code=error.status_code, content=error.to_body())
        await response(scope, receive, send)


def reject_upload(headers: Headers) -> AppError | None:
    """Причина отказа по одним заголовкам; ``None`` — пропустить в приложение."""
    length = headers.get("content-length", "")
    limit = settings.upload_ct_max_bytes
    if length.isdigit() and int(length) > limit + _MULTIPART_MARGIN:
        return AppError(
            f"Файл больше {limit // (1024 * 1024)} МБ",
            code="file_too_large",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        )
    scheme, _, token = headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return UnauthorizedError("Требуется авторизация")
    try:
        payload = decode_token(token.strip())
    except jwt.PyJWTError:
        return UnauthorizedError("Невалидный токен", code="invalid_token")
    if payload.get("type") != "access":
        return UnauthorizedError("Ожидался access-токен", code="invalid_token")
    return None
