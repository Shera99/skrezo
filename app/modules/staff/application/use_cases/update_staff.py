"""Use-case: правка сотрудника (ФИО, телефон, специализация, роль).

Смену телефона работника делает владелец/менеджер (право staff.manage) — сам
работник свой номер менять не может. Владельца через этот флоу править нельзя.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.modules.staff.application.services.roles import resolve_assignable_role
from app.modules.staff.domain.models import StaffMember
from app.modules.staff.infrastructure.repositories.staff_repository import StaffRepository
from app.shared.domain.value_objects import normalize_phone


class UpdateStaffUseCase:
    def __init__(self, session: AsyncSession, staff: StaffRepository) -> None:
        self._session = session
        self._staff = staff

    async def execute(
        self,
        clinic_id: uuid.UUID,
        membership_id: uuid.UUID,
        *,
        full_name: str | None = None,
        phone_raw: str | None = None,
        specialization_id: uuid.UUID | None = None,
        role_id: uuid.UUID | None = None,
    ) -> StaffMember:
        membership = await self._staff.get_membership(clinic_id, membership_id)
        if membership is None:
            raise NotFoundError("Сотрудник не найден", code="staff_not_found")
        role = await self._staff.get_role(membership.role_id)
        if role is not None and role.is_owner:
            raise ForbiddenError("Владельца нельзя редактировать здесь", code="cannot_edit_owner")

        account = await self._staff.get_account(membership.account_id)
        if account is None:
            raise NotFoundError("Аккаунт сотрудника не найден", code="staff_not_found")

        if full_name is not None:
            account.full_name = full_name
        if phone_raw is not None:
            phone = normalize_phone(phone_raw)
            existing = await self._staff.account_by_phone(phone)
            if existing is not None and existing.id != account.id:
                raise ConflictError("Этот номер уже используется", code="phone_already_used")
            account.phone = phone
        if specialization_id is not None:
            if not await self._staff.specialization_exists(specialization_id):
                raise ValidationAppError(
                    "Специализация не найдена", code="specialization_not_found"
                )
            membership.specialization_id = specialization_id
        if role_id is not None:
            # Роль — это права и потолок скидки сотрудника; кеш прав привязан к
            # роли, а не к членству, поэтому сбрасывать его здесь нечего.
            membership.role_id = await resolve_assignable_role(clinic_id, role_id, self._staff)

        await self._session.commit()
        members = await self._staff.list_members(clinic_id)
        return next(m for m in members if m.membership_id == membership_id)
