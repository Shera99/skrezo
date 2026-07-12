"""Контекст аутентифицированного пользователя из JWT (единый контур).

Access-токен несёт `sub` (account_id), опционально `clinic_id`+`role_id` (скоуп
выбранной клиники) и `platform_admin` (сотрудник команды Skrezo). Зависимость
:func:`get_client_context` декодирует его и отдаёт типизированный контекст;
проверку прав делают ``access`` (клиника) и платформенный гвард поверх него.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import UnauthorizedError
from app.core.security import decode_token

_bearer = HTTPBearer(auto_error=False, description="Bearer-токен (единый контур)")


@dataclass(frozen=True, slots=True)
class ClientContext:
    """Кто выполняет запрос: аккаунт, скоуп клиники/роли, признак админа Skrezo."""

    account_id: uuid.UUID
    clinic_id: uuid.UUID | None
    role_id: uuid.UUID | None
    is_platform_admin: bool = False


def _optional_uuid(value: object) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


def get_client_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> ClientContext:
    """Проверить access-токен и вернуть контекст."""
    if credentials is None:
        raise UnauthorizedError("Требуется авторизация")
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Невалидный токен", code="invalid_token") from exc
    if payload.get("type") != "access":
        raise UnauthorizedError("Ожидался access-токен", code="invalid_token")
    return ClientContext(
        account_id=uuid.UUID(str(payload["sub"])),
        clinic_id=_optional_uuid(payload.get("clinic_id")),
        role_id=_optional_uuid(payload.get("role_id")),
        is_platform_admin=bool(payload.get("platform_admin")),
    )
