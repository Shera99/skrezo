"""Use-case: вернуть исключённого сотрудника в клинику.

Удаление сотрудника — деактивация членства (на нём висят приёмы и платежи), а
повторно завести того же человека нельзя: номер уже занят его аккаунтом (409
``phone_already_used``). Восстановление снова включает то же членство — с той
же ролью и специальностью — если в тарифе есть свободное место.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.access.application.services.membership_resolver import MembershipResolver
from app.modules.staff.domain.models import StaffMember
from app.modules.staff.infrastructure.repositories.staff_repository import StaffRepository


class RestoreStaffUseCase:
    def __init__(
        self,
        session: AsyncSession,
        staff: StaffRepository,
        memberships: MembershipResolver,
    ) -> None:
        self._session = session
        self._staff = staff
        self._memberships = memberships

    async def execute(self, clinic_id: uuid.UUID, membership_id: uuid.UUID) -> StaffMember:
        """Включить членство обратно; уже активный сотрудник — без изменений.

        Место в тарифе проверяется так же, как при добавлении: все активные
        сотрудники, кроме владельца. Нет места — 409 ``seats_limit_reached``.
        """
        membership = await self._staff.get_membership(clinic_id, membership_id)
        if membership is None:
            raise NotFoundError("Сотрудник не найден", code="staff_not_found")
        if not membership.active:
            clinic = await self._staff.get_clinic(clinic_id)
            if (
                clinic is not None
                and clinic.seats_limit is not None
                and await self._staff.count_seats_used(clinic_id) >= clinic.seats_limit
            ):
                raise ConflictError("Достигнут лимит мест по тарифу", code="seats_limit_reached")
            membership.active = True
            await self._session.commit()
            # Только после коммита: гвард берёт членство из кеша, и сброс раньше
            # дал бы кешу заполниться прежним «исключён» из незакрытой транзакции.
            await self._memberships.invalidate(membership.account_id, clinic_id)
        members = await self._staff.list_members(clinic_id)
        return next(m for m in members if m.membership_id == membership_id)
