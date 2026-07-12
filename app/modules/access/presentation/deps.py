"""Зависимости RBAC: резолвер прав и guard ``require_permission``.

``require_permission("key")`` — фабрика FastAPI-зависимости для защиты ручек
клиентского контура. Владелец (``is_owner``) проходит любую проверку.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ForbiddenError
from app.core.redis import get_redis
from app.modules.access.application.services.permission_resolver import PermissionResolver
from app.modules.access.infrastructure.repositories.role_repository import RoleRepository
from app.shared.auth import ClientContext, get_client_context


def get_role_repository(session: AsyncSession = Depends(get_session)) -> RoleRepository:
    return RoleRepository(session)


def get_permission_resolver(
    role_repository: RoleRepository = Depends(get_role_repository),
    redis: Redis = Depends(get_redis),
) -> PermissionResolver:
    return PermissionResolver(role_repository, redis)


def require_permission(
    permission_key: str,
) -> Callable[..., Awaitable[ClientContext]]:
    """Вернуть зависимость, требующую у текущего клиента право ``permission_key``."""

    async def _guard(
        context: ClientContext = Depends(get_client_context),
        resolver: PermissionResolver = Depends(get_permission_resolver),
    ) -> ClientContext:
        if context.role_id is None:
            raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
        resolved = await resolver.resolve(context.role_id)
        if resolved is None:
            raise ForbiddenError("Роль не найдена")
        if resolved.is_owner or permission_key in resolved.permissions:
            return context
        raise ForbiddenError("Недостаточно прав")

    return _guard
