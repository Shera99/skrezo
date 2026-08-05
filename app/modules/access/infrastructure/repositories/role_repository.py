"""Репозиторий ролей: чтение роли и её набора прав."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.permission import Permission
from app.shared.db.models.role import Role
from app.shared.db.models.role_permission import RolePermission


class RoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_permission_keys(
        self, role_id: uuid.UUID
    ) -> tuple[bool, list[str], int] | None:
        """Вернуть (is_owner, ключи прав, потолок скидки) роли или None, если роли нет."""
        role = await self._session.get(Role, role_id)
        if role is None:
            return None
        keys = (
            (
                await self._session.execute(
                    select(Permission.key)
                    .join(RolePermission, RolePermission.permission_id == Permission.id)
                    .where(RolePermission.role_id == role_id)
                )
            )
            .scalars()
            .all()
        )
        return role.is_owner, list(keys), role.max_discount_percent
