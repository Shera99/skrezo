"""Схемы модуля доступа: триал-функции (админка) и управление ролями (клиника)."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.modules.access.domain.models import RoleRow
from app.shared.api.schemas import CamelModel
from app.shared.db.models.permission import Permission


class TrialFeatureItem(CamelModel):
    """Право каталога с флагом доступности на триале."""

    id: uuid.UUID
    key: str = Field(examples=["schedule.view_all"])
    group_key: str = Field(examples=["schedule"])
    description: str = Field(examples=["Видеть расписание всех врачей"])
    in_trial: bool = Field(description="Доступно ли право на 30-дневном триале")

    @classmethod
    def from_permission(cls, p: Permission) -> TrialFeatureItem:
        return cls(
            id=p.id,
            key=p.key,
            group_key=p.group_key,
            description=p.description,
            in_trial=p.in_trial,
        )


class TrialFeaturesResponse(CamelModel):
    """Каталог прав с флагами триала (сгруппировать по ``groupKey`` на фронте)."""

    items: list[TrialFeatureItem]


class TrialFeatureUpdateItem(CamelModel):
    """Переключение флага у одного права."""

    key: str = Field(description="Ключ права", examples=["reports.view_all"])
    in_trial: bool = Field(description="Сделать ли доступным на триале")


class UpdateTrialFeaturesRequest(CamelModel):
    """Пакетная настройка набора триал-функций."""

    items: list[TrialFeatureUpdateItem] = Field(min_length=1)


class PermissionItem(CamelModel):
    """Право каталога — чекбокс в конструкторе роли."""

    key: str = Field(examples=["cash.operate"])
    group_key: str = Field(description="Группа для UI", examples=["cash"])
    description: str = Field(examples=["Проведение платежей и долгов"])

    @classmethod
    def from_permission(cls, p: Permission) -> PermissionItem:
        return cls(key=p.key, group_key=p.group_key, description=p.description)


class PermissionsResponse(CamelModel):
    """Каталог прав для настройки ролей (группировать по ``groupKey``)."""

    items: list[PermissionItem]


class RoleItem(CamelModel):
    """Роль клиники с набором прав."""

    id: uuid.UUID
    key: str | None = Field(default=None, description="Ключ системной роли; null у кастомных")
    name: str = Field(examples=["Администратор ресепшена"])
    is_system: bool = Field(description="Системная роль (владелец/менеджер/работник)")
    is_owner: bool = Field(description="Роль владельца — полный доступ, не редактируется")
    permissions: list[str] = Field(description="Ключи прав роли")
    members_count: int = Field(description="Сотрудников на этой роли")
    editable_permissions: bool = Field(description="Можно ли менять права роли")
    deletable: bool = Field(description="Можно ли удалить роль")

    @classmethod
    def from_row(cls, r: RoleRow) -> RoleItem:
        return cls(
            id=r.id,
            key=r.key,
            name=r.name,
            is_system=r.is_system,
            is_owner=r.is_owner,
            permissions=r.permissions,
            members_count=r.members_count,
            editable_permissions=r.editable_permissions,
            deletable=r.deletable,
        )


class RolesResponse(CamelModel):
    """Роли клиники (владелец и системные — первыми)."""

    items: list[RoleItem]


class CreateRoleRequest(CamelModel):
    """Создание кастомной роли клиники."""

    name: str = Field(min_length=2, max_length=100, examples=["Администратор ресепшена"])
    permissions: list[str] = Field(
        default_factory=list, description="Ключи прав из GET /clinic/permissions"
    )


class UpdateRoleRequest(CamelModel):
    """Правка роли: переданы только изменяемые поля.

    ``permissions`` заменяет набор прав целиком. Системную роль переименовать
    нельзя, роль владельца не редактируется вовсе.
    """

    name: str | None = Field(default=None, min_length=2, max_length=100)
    permissions: list[str] | None = Field(default=None, description="Полный новый набор прав")


class RoleDeletedResponse(CamelModel):
    """Подтверждение удаления роли."""

    id: uuid.UUID
    deleted: bool = True
