"""Клонирование глобальных шаблонов ролей в роли конкретной клиники.

Вызывается при создании клиники (провижининг). Возвращает id роли владельца —
на неё вешается членство администратора.
"""

from __future__ import annotations

import uuid

from app.modules.access.infrastructure.repositories.role_provisioning_repository import (
    RoleProvisioningRepository,
)


class RoleProvisioningService:
    def __init__(self, repository: RoleProvisioningRepository) -> None:
        self._repo = repository

    async def clone_templates_for_clinic(self, clinic_id: uuid.UUID) -> uuid.UUID:
        templates = await self._repo.list_global_templates()
        owner_role_id: uuid.UUID | None = None
        for template in templates:
            role = await self._repo.clone_role(template, clinic_id)
            if template.is_owner:
                owner_role_id = role.id
        if owner_role_id is None:
            raise RuntimeError("Не найден шаблон роли владельца — прогоните сид ролей (0004).")
        return owner_role_id
