"""Use-case: удаление работника.

v1 — деактивация (доступ закрыт, членство остаётся снапшотом). Полное удаление
при отсутствии истории приёмов/платежей появится с клиническим ядром.
Владельца удалить нельзя.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.staff.infrastructure.repositories.staff_repository import StaffRepository


class RemoveStaffUseCase:
    def __init__(self, session: AsyncSession, staff: StaffRepository) -> None:
        self._session = session
        self._staff = staff

    async def execute(self, clinic_id: uuid.UUID, membership_id: uuid.UUID) -> str:
        membership = await self._staff.get_membership(clinic_id, membership_id)
        if membership is None:
            raise NotFoundError("Сотрудник не найден", code="staff_not_found")
        role = await self._staff.get_role(membership.role_id)
        if role is not None and role.is_owner:
            raise ForbiddenError("Владельца нельзя удалить", code="cannot_remove_owner")

        membership.active = False
        await self._session.commit()
        return "deactivated"
