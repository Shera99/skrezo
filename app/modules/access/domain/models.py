"""Доменные структуры управления ролями."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RoleRow:
    """Роль клиники с набором прав и числом сотрудников на ней."""

    id: uuid.UUID
    key: str | None  # ключ системной роли (owner/manager/worker); None у кастомных
    name: str
    is_system: bool
    is_owner: bool
    permissions: list[str]
    members_count: int

    @property
    def editable_permissions(self) -> bool:
        """Можно ли править права роли: у владельца доступ всегда полный."""
        return not self.is_owner

    @property
    def deletable(self) -> bool:
        """Удалять можно только кастомные роли без сотрудников."""
        return not self.is_system and not self.is_owner and self.members_count == 0
