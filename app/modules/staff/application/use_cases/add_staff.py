"""Use-case: добавить сотрудника (владелец/менеджер).

По умолчанию — роль «работник» клиники + специализация; можно сразу выдать
другую роль клиники (``role_id``), например «менеджер». Проверки: клиника не
соло, специализация существует, лимит мест, дедуп телефона.

Аккаунт создаётся **без пароля**: сотрудник входит по коду на свой номер.
Передавать ему нечего — уведомление лишь сообщает, что доступ открыт.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError, ValidationAppError
from app.modules.staff.application.services.roles import resolve_assignable_role
from app.modules.staff.application.services.staff_notification_service import (
    StaffNotificationService,
)
from app.modules.staff.infrastructure.repositories.staff_repository import StaffRepository
from app.shared.db.enums import ClinicType
from app.shared.db.models.membership import Membership
from app.shared.domain.value_objects import normalize_phone


@dataclass(slots=True)
class AddStaffResult:
    membership: Membership


class AddStaffUseCase:
    def __init__(
        self,
        session: AsyncSession,
        staff: StaffRepository,
        notifier: StaffNotificationService,
    ) -> None:
        self._session = session
        self._staff = staff
        self._notifier = notifier

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        full_name: str,
        phone_raw: str,
        specialization_id: uuid.UUID,
        role_id: uuid.UUID | None = None,
    ) -> AddStaffResult:
        clinic = await self._staff.get_clinic(clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        if clinic.type is ClinicType.SOLO:
            raise BadRequestError("Соло-аккаунт не поддерживает сотрудников", code="solo_no_staff")
        if not await self._staff.specialization_exists(specialization_id):
            raise ValidationAppError("Специализация не найдена", code="specialization_not_found")
        if (
            clinic.seats_limit is not None
            and await self._staff.count_practitioners(clinic_id) >= clinic.seats_limit
        ):
            raise ConflictError("Достигнут лимит мест по тарифу", code="seats_limit_reached")

        phone = normalize_phone(phone_raw)
        if await self._staff.account_by_phone(phone) is not None:
            raise ConflictError("Этот номер уже используется", code="phone_already_used")

        if role_id is not None:
            assigned_role_id = await resolve_assignable_role(clinic_id, role_id, self._staff)
        else:
            worker_role_id = await self._staff.worker_role_id(clinic_id)
            if worker_role_id is None:
                raise NotFoundError("У клиники нет роли «работник»", code="worker_role_missing")
            assigned_role_id = worker_role_id

        account = await self._staff.create_account(phone=phone, full_name=full_name)
        membership = await self._staff.create_membership(
            clinic_id=clinic_id,
            account_id=account.id,
            role_id=assigned_role_id,
            specialization_id=specialization_id,
        )
        await self._session.commit()
        await self._notifier.notify_staff_added(phone=phone, clinic_name=clinic.name)
        return AddStaffResult(membership=membership)
