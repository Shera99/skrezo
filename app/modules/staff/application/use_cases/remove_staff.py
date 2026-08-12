"""Use-case: удаление работника.

v1 — деактивация (членство остаётся снапшотом: на нём висят приёмы и платежи).
Полное удаление при отсутствии истории появится с клиническим ядром. Владельца
удалить нельзя.

Аккаунт при этом **сохраняется**: он глобальный, и тот же врач может работать в
другой клинике. Закрывается ровно доступ к этой — сбросом кеша членства, после
которого гвард ``get_clinic_context`` перестаёт пускать его токен.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.access.application.services.membership_resolver import MembershipResolver
from app.modules.staff.infrastructure.repositories.staff_repository import StaffRepository


@dataclass(slots=True)
class RemoveStaffResult:
    mode: str
    account_id: uuid.UUID


class RemoveStaffUseCase:
    def __init__(
        self,
        session: AsyncSession,
        staff: StaffRepository,
        memberships: MembershipResolver,
    ) -> None:
        self._session = session
        self._staff = staff
        self._memberships = memberships

    async def execute(self, clinic_id: uuid.UUID, membership_id: uuid.UUID) -> RemoveStaffResult:
        membership = await self._staff.get_membership(clinic_id, membership_id)
        if membership is None:
            raise NotFoundError("Сотрудник не найден", code="staff_not_found")
        role = await self._staff.get_role(membership.role_id)
        if role is not None and role.is_owner:
            raise ForbiddenError("Владельца нельзя удалить", code="cannot_remove_owner")

        account_id = membership.account_id
        membership.active = False
        await self._session.commit()
        # Только после коммита: сбросить кеш раньше — значит дать ему шанс
        # заполниться прежним значением из ещё не закрытой транзакции.
        await self._memberships.invalidate(account_id, clinic_id)
        return RemoveStaffResult(mode="deactivated", account_id=account_id)
