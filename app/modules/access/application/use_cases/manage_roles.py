"""Use-cases управления ролями клиники (право ``roles.manage``).

Инварианты, которые здесь защищаются:

* **роль владельца не редактируется** — у неё всегда полный доступ (иначе клинику
  можно запереть, отобрав права у единственного администратора);
* **системные роли** (менеджер/работник) правят только НАБОР ПРАВ: название
  приходит из каталога сообщений по ``key``, переименование и удаление закрыты;
* **роль с сотрудниками не удаляется** (``membership.role_id`` — FK без каскада,
  БД иначе отдала бы 500 вместо человекочитаемой ошибки);
* после любой правки прав сбрасывается кеш ``PermissionResolver`` — иначе роль
  ходила бы со старыми правами до истечения TTL.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.access.application.services.permission_resolver import PermissionResolver
from app.modules.access.domain.models import RoleRow
from app.modules.access.infrastructure.repositories.role_admin_repository import (
    RoleAdminRepository,
)
from app.shared.db.models.permission import Permission
from app.shared.db.models.role import Role


class ListRolesUseCase:
    def __init__(self, roles: RoleAdminRepository) -> None:
        self._roles = roles

    async def execute(self, clinic_id: uuid.UUID) -> list[RoleRow]:
        """Роли клиники с правами и числом сотрудников."""
        return await self._roles.list_roles(clinic_id)


class ListPermissionsUseCase:
    def __init__(self, roles: RoleAdminRepository) -> None:
        self._roles = roles

    async def execute(self) -> Sequence[Permission]:
        """Каталог прав — чекбоксы в конструкторе роли (группировать по ``groupKey``)."""
        return await self._roles.all_permissions()


class _RoleWriteBase:
    def __init__(
        self,
        session: AsyncSession,
        roles: RoleAdminRepository,
        resolver: PermissionResolver,
    ) -> None:
        self._session = session
        self._roles = roles
        self._resolver = resolver

    async def _resolve_permission_ids(self, keys: Sequence[str]) -> list[uuid.UUID]:
        """Ключи прав → id; неизвестный ключ отвергается целиком (защита от опечаток)."""
        found = await self._roles.permission_ids_by_keys(keys)
        missing = [k for k in keys if k not in found]
        if missing:
            raise ValidationAppError(
                f"Право не найдено: {', '.join(sorted(missing))}", code="permission_not_found"
            )
        return [found[k] for k in keys]

    async def _get_editable(self, clinic_id: uuid.UUID, role_id: uuid.UUID) -> Role:
        role = await self._roles.get_role(clinic_id, role_id)
        if role is None:
            raise NotFoundError("Роль не найдена", code="role_not_found")
        if role.is_owner:
            raise ConflictError(
                "Роль владельца не редактируется — у неё всегда полный доступ",
                code="owner_role_immutable",
            )
        return role

    async def _ensure_name_free(
        self, clinic_id: uuid.UUID, name: str, *, exclude_role_id: uuid.UUID | None = None
    ) -> None:
        if await self._roles.name_taken(clinic_id, name, exclude_role_id=exclude_role_id):
            raise ConflictError("Роль с таким названием уже есть", code="role_name_taken")


class CreateRoleUseCase(_RoleWriteBase):
    async def execute(
        self, clinic_id: uuid.UUID, name: str, permission_keys: Sequence[str]
    ) -> RoleRow:
        """Создать кастомную роль клиники с заданным набором прав."""
        await self._ensure_name_free(clinic_id, name)
        permission_ids = await self._resolve_permission_ids(permission_keys)

        role = await self._roles.create_role(clinic_id, name)
        await self._roles.replace_permissions(role.id, permission_ids)
        await self._session.commit()
        return await _row_of(self._roles, clinic_id, role.id)


class UpdateRoleUseCase(_RoleWriteBase):
    async def execute(
        self,
        clinic_id: uuid.UUID,
        role_id: uuid.UUID,
        *,
        name: str | None,
        permission_keys: Sequence[str] | None,
    ) -> RoleRow:
        """Переименовать роль и/или заменить её набор прав.

        Системные роли переименовать нельзя (название берётся из каталога по
        ``key``), но их права настраиваются пер-клиника — это и есть смысл RBAC.
        """
        role = await self._get_editable(clinic_id, role_id)

        if name is not None and name != role.name:
            if role.is_system:
                raise ConflictError(
                    "Системную роль нельзя переименовать", code="system_role_rename"
                )
            await self._ensure_name_free(clinic_id, name, exclude_role_id=role_id)
            role.name = name

        if permission_keys is not None:
            permission_ids = await self._resolve_permission_ids(permission_keys)
            await self._roles.replace_permissions(role_id, permission_ids)

        await self._session.commit()
        # Права роли изменились — сбрасываем кеш, иначе доступ поедет по старым.
        await self._resolver.invalidate(role_id)
        return await _row_of(self._roles, clinic_id, role_id)


class DeleteRoleUseCase(_RoleWriteBase):
    async def execute(self, clinic_id: uuid.UUID, role_id: uuid.UUID) -> None:
        """Удалить кастомную роль, на которой нет сотрудников."""
        role = await self._get_editable(clinic_id, role_id)
        if role.is_system:
            raise ConflictError("Системную роль нельзя удалить", code="system_role_delete")

        members = await self._roles.members_count(role_id)
        if members:
            raise ConflictError(
                "На роли есть сотрудники — сначала переведите их на другую роль",
                code="role_has_members",
                extra={"membersCount": members},
            )

        await self._roles.delete_role(role)
        await self._session.commit()
        await self._resolver.invalidate(role_id)


async def _row_of(
    roles: RoleAdminRepository, clinic_id: uuid.UUID, role_id: uuid.UUID
) -> RoleRow:
    """Перечитать роль как строку выдачи (с правами и счётчиком сотрудников)."""
    rows = await roles.list_roles(clinic_id)
    row = next((r for r in rows if r.id == role_id), None)
    if row is None:  # pragma: no cover — только что записали
        raise NotFoundError("Роль не найдена", code="role_not_found")
    return row
