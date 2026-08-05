"""Репозиторий клонирования ролей: глобальные шаблоны → роли клиники."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.role import Role
from app.shared.db.models.role_permission import RolePermission


class RoleProvisioningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_global_templates(self) -> Sequence[Role]:
        return (
            (
                await self._session.execute(
                    select(Role).where(Role.clinic_id.is_(None), Role.is_system.is_(True))
                )
            )
            .scalars()
            .all()
        )

    async def clone_role(self, template: Role, clinic_id: uuid.UUID) -> Role:
        role = Role(
            clinic_id=clinic_id,
            key=template.key,
            name=template.name,
            is_system=True,
            is_owner=template.is_owner,
            # Потолок скидки — часть шаблона роли, а не отдельная настройка.
            max_discount_percent=template.max_discount_percent,
        )
        self._session.add(role)
        await self._session.flush()

        permission_ids = (
            (
                await self._session.execute(
                    select(RolePermission.permission_id).where(
                        RolePermission.role_id == template.id
                    )
                )
            )
            .scalars()
            .all()
        )
        for permission_id in permission_ids:
            self._session.add(RolePermission(role_id=role.id, permission_id=permission_id))
        await self._session.flush()
        return role
