"""Назначение роли сотруднику — общая проверка для приёма на работу и правки.

Роль сотрудника — это его права и потолок скидки, поэтому назначать её вправе
только владелец/менеджер (право ``staff.manage``) и только из ролей СВОЕЙ
клиники. Роль владельца через этот путь не выдаётся: владелец в клинике один,
и второй появился бы мимо провижининга.
"""

from __future__ import annotations

import uuid

from app.core.errors import ConflictError, ValidationAppError
from app.modules.staff.infrastructure.repositories.staff_repository import StaffRepository


async def resolve_assignable_role(
    clinic_id: uuid.UUID, role_id: uuid.UUID, staff: StaffRepository
) -> uuid.UUID:
    """Проверить, что роль принадлежит клинике и её можно назначить."""
    role = await staff.role_in_clinic(clinic_id, role_id)
    if role is None:
        raise ValidationAppError("Роль не найдена в этой клинике", code="role_not_found")
    if role.is_owner:
        raise ConflictError(
            "Роль владельца не назначается — владелец в клинике один",
            code="cannot_assign_owner_role",
        )
    return role.id
