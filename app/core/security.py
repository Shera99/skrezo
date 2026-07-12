"""Криптографические примитивы: хеширование паролей и работа с JWT.

Единый контур авторизации: один секрет и один ``aud`` для всех (команда Skrezo и
сотрудники клиник входят одинаково — телефон + пароль). Роль/доступ разграничивают
claims токена: ``platform_admin`` (кросс-тенант) и ``clinic_id``/``role_id`` (скоуп
клиники).
"""

from __future__ import annotations

import enum
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

_password_hasher = PasswordHasher()

# Алфавит временного пароля: без похожих символов (0/O, 1/l/I) — чтобы диктовать.
_TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


# ── Пароли (общий контур: команда Skrezo и аккаунты клиник) ─────────────────
def hash_password(password: str) -> str:
    """Захешировать пароль (argon2id)."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Проверить пароль против хеша."""
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def generate_temp_password(length: int = 10) -> str:
    """Сгенерировать читаемый временный пароль.

    Используется при создании владельца (провижининг) и работника (staff):
    выдаётся один раз, аккаунт помечается ``must_change_password`` и обязан
    сменить пароль при первом входе.
    """
    return "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(length))


# ── JWT ─────────────────────────────────────────────────────────────────────
_AUDIENCE = "skrezo"


class TokenType(enum.StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def create_token(
    *,
    subject: str,
    token_type: TokenType,
    ttl_seconds: int | None = None,
    jti: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Собрать подписанный JWT единого контура.

    :param subject: идентификатор субъекта (``account.id``)
    :param token_type: access или refresh
    :param ttl_seconds: срок жизни; по умолчанию — из настроек по типу токена
    :param jti: идентификатор токена; если не задан — генерируется (нужен, чтобы
        сохранить refresh-jti в allowlist Redis и уметь его отозвать)
    :param extra_claims: доп. полезная нагрузка (``platform_admin``,
        ``clinic_id``, ``role_id``)
    """
    now = datetime.now(UTC)
    if ttl_seconds is None:
        ttl_seconds = (
            settings.access_token_ttl_seconds
            if token_type is TokenType.ACCESS
            else settings.refresh_token_ttl_seconds
        )
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type.value,
        "aud": _AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "jti": jti or uuid.uuid4().hex,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Проверить подпись/срок и вернуть payload.

    Бросает :class:`jwt.PyJWTError` при невалидном токене — вызывающий слой
    транслирует это в 401.
    """
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        audience=_AUDIENCE,
    )
