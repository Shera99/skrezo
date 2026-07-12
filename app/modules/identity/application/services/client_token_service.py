"""Сервис токенов единого контура авторизации.

Один токен для всех: команда Skrezo (claim ``platform_admin``) и сотрудники клиник
(claims ``clinic_id`` + ``role_id`` — контекст активной клиники, когда выбрана).
Refresh-``jti`` — в allowlist Redis (отзыв через logout).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from redis.asyncio import Redis

from app.core.config import settings
from app.core.errors import UnauthorizedError
from app.core.security import TokenType, create_token, decode_token

_REFRESH_PREFIX = "auth:refresh:"


@dataclass(slots=True)
class IssuedClientTokens:
    access_token: str
    refresh_token: str
    expires_in: int


def _token_claims(
    clinic_id: str | None, role_id: str | None, platform_admin: bool
) -> dict[str, Any]:
    claims: dict[str, Any] = {}
    if clinic_id:
        claims["clinic_id"] = clinic_id
    if role_id:
        claims["role_id"] = role_id
    if platform_admin:
        claims["platform_admin"] = True
    return claims


class ClientTokenService:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def issue(
        self,
        account_id: str,
        clinic_id: str | None = None,
        role_id: str | None = None,
        platform_admin: bool = False,
    ) -> IssuedClientTokens:
        jti = uuid.uuid4().hex
        claims = _token_claims(clinic_id, role_id, platform_admin)
        access = create_token(
            subject=account_id,
            token_type=TokenType.ACCESS,
            extra_claims=claims or None,
        )
        refresh = create_token(
            subject=account_id,
            token_type=TokenType.REFRESH,
            jti=jti,
            extra_claims=claims or None,
        )
        await self._redis.set(
            _REFRESH_PREFIX + jti, account_id, ex=settings.refresh_token_ttl_seconds
        )
        return IssuedClientTokens(access, refresh, settings.access_token_ttl_seconds)

    async def refresh_access(self, refresh_token: str) -> tuple[str, int]:
        payload = self._decode(refresh_token)
        if payload.get("type") != TokenType.REFRESH.value:
            raise UnauthorizedError("Ожидался refresh-токен", code="invalid_token")
        jti = payload.get("jti")
        subject = payload.get("sub")
        stored = await self._redis.get(_REFRESH_PREFIX + jti) if jti else None
        if stored is None or stored != subject:
            raise UnauthorizedError("Сессия недействительна", code="invalid_token")
        # Сохраняем контекст клиники/роли/платформы в новом access-токене.
        claims = _token_claims(
            payload.get("clinic_id"), payload.get("role_id"), bool(payload.get("platform_admin"))
        )
        access = create_token(
            subject=subject,
            token_type=TokenType.ACCESS,
            extra_claims=claims or None,
        )
        return access, settings.access_token_ttl_seconds

    async def revoke(self, refresh_token: str) -> None:
        try:
            payload = self._decode(refresh_token)
        except UnauthorizedError:
            return
        jti = payload.get("jti")
        if jti:
            await self._redis.delete(_REFRESH_PREFIX + jti)

    @staticmethod
    def _decode(token: str) -> dict[str, Any]:
        try:
            return decode_token(token)
        except jwt.PyJWTError as exc:
            raise UnauthorizedError("Невалидный токен", code="invalid_token") from exc
