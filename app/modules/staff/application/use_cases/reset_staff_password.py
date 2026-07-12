"""Use-case: сброс пароля работника (владелец/менеджер, право staff.manage).

Работнику выдаётся новый **временный** пароль (`must_change_password=true`),
возвращается один раз для передачи. Владельца через этот флоу не сбрасывают.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.core.security import generate_temp_password, hash_password
from app.modules.staff.infrastructure.repositories.staff_repository import StaffRepository


class ResetStaffPasswordUseCase:
    def __init__(self, session: AsyncSession, staff: StaffRepository) -> None:
        self._session = session
        self._staff = staff

    async def execute(self, clinic_id: uuid.UUID, membership_id: uuid.UUID) -> str:
        membership = await self._staff.get_membership(clinic_id, membership_id)
        if membership is None:
            raise NotFoundError("Сотрудник не найден", code="staff_not_found")
        role = await self._staff.get_role(membership.role_id)
        if role is not None and role.is_owner:
            raise ForbiddenError("Владельца нельзя сбрасывать здесь", code="cannot_edit_owner")
        account = await self._staff.get_account(membership.account_id)
        if account is None:
            raise NotFoundError("Аккаунт сотрудника не найден", code="staff_not_found")

        temp_password = generate_temp_password()
        account.password_hash = hash_password(temp_password)
        account.must_change_password = True
        await self._session.commit()
        return temp_password
