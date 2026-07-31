"""Репозиторий управления ролями клиники: список, создание, правка, удаление.

Все выборки скоупятся клиникой из контекста токена: глобальные шаблоны
(``clinic_id IS NULL``) через это API не видны и не правятся — они клонируются
только при создании клиники.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.access.domain.models import RoleRow
from app.shared.db.models.membership import Membership
from app.shared.db.models.permission import Permission
from app.shared.db.models.role import Role
from app.shared.db.models.role_permission import RolePermission


class RoleAdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Чтение ─────────────────────────────────────────────────────────────
    async def list_roles(self, clinic_id: uuid.UUID) -> list[RoleRow]:
        """Роли клиники с их правами и числом сотрудников (системные — первыми)."""
        roles = (
            (
                await self._session.execute(
                    select(Role)
                    .where(Role.clinic_id == clinic_id)
                    .order_by(Role.is_owner.desc(), Role.is_system.desc(), Role.name)
                )
            )
            .scalars()
            .all()
        )
        if not roles:
            return []

        role_ids = [r.id for r in roles]
        permission_rows = (
            await self._session.execute(
                select(RolePermission.role_id, Permission.key)
                .join(Permission, Permission.id == RolePermission.permission_id)
                .where(RolePermission.role_id.in_(role_ids))
            )
        ).all()
        keys_by_role: dict[uuid.UUID, list[str]] = {}
        for role_id, key in permission_rows:
            keys_by_role.setdefault(role_id, []).append(key)

        member_rows = (
            await self._session.execute(
                select(Membership.role_id, func.count())
                .where(Membership.role_id.in_(role_ids), Membership.active.is_(True))
                .group_by(Membership.role_id)
            )
        ).all()
        members_by_role = {role_id: int(count) for role_id, count in member_rows}

        return [
            RoleRow(
                id=role.id,
                key=role.key,
                name=role.name,
                is_system=role.is_system,
                is_owner=role.is_owner,
                permissions=sorted(keys_by_role.get(role.id, [])),
                members_count=members_by_role.get(role.id, 0),
            )
            for role in roles
        ]

    async def get_role(self, clinic_id: uuid.UUID, role_id: uuid.UUID) -> Role | None:
        """Роль ЭТОЙ клиники по id (скоуп закрывает IDOR)."""
        return (
            await self._session.execute(
                select(Role).where(Role.id == role_id, Role.clinic_id == clinic_id)
            )
        ).scalar_one_or_none()

    async def name_taken(
        self, clinic_id: uuid.UUID, name: str, *, exclude_role_id: uuid.UUID | None = None
    ) -> bool:
        """Есть ли уже роль с таким названием в клинике (без учёта регистра)."""
        stmt = select(Role.id).where(
            Role.clinic_id == clinic_id, func.lower(Role.name) == name.lower()
        )
        if exclude_role_id is not None:
            stmt = stmt.where(Role.id != exclude_role_id)
        return (await self._session.execute(stmt)).first() is not None

    async def members_count(self, role_id: uuid.UUID) -> int:
        """Сколько активных сотрудников на роли (удалять роль с людьми нельзя)."""
        count = (
            await self._session.execute(
                select(func.count()).where(
                    Membership.role_id == role_id, Membership.active.is_(True)
                )
            )
        ).scalar_one()
        return int(count)

    # ── Каталог прав ───────────────────────────────────────────────────────
    async def all_permissions(self) -> Sequence[Permission]:
        """Каталог прав (для чекбоксов в UI и валидации ключей)."""
        return (
            (
                await self._session.execute(
                    select(Permission).order_by(Permission.sort_order, Permission.key)
                )
            )
            .scalars()
            .all()
        )

    async def permission_ids_by_keys(self, keys: Sequence[str]) -> dict[str, uuid.UUID]:
        """Отобразить ключи прав в их id (для сверки с каталогом)."""
        if not keys:
            return {}
        rows = (
            await self._session.execute(
                select(Permission.key, Permission.id).where(Permission.key.in_(keys))
            )
        ).all()
        return {r[0]: r[1] for r in rows}

    # ── Запись ─────────────────────────────────────────────────────────────
    async def create_role(self, clinic_id: uuid.UUID, name: str) -> Role:
        """Создать кастомную роль клиники (не системная, не владелец)."""
        role = Role(clinic_id=clinic_id, key=None, name=name, is_system=False, is_owner=False)
        self._session.add(role)
        await self._session.flush()
        return role

    async def replace_permissions(
        self, role_id: uuid.UUID, permission_ids: Sequence[uuid.UUID]
    ) -> None:
        """Заменить набор прав роли целиком (проще и предсказуемее частичных правок)."""
        await self._session.execute(
            delete(RolePermission).where(RolePermission.role_id == role_id)
        )
        await self._session.flush()
        for permission_id in permission_ids:
            self._session.add(RolePermission(role_id=role_id, permission_id=permission_id))
        await self._session.flush()

    async def delete_role(self, role: Role) -> None:
        """Удалить роль (связи role_permission уходят каскадом)."""
        await self._session.delete(role)
        await self._session.flush()
