"""Репозиторий сотрудников клиники (клиентская сторона).

Работники — практики с ролью «работник» и специализацией. Число мест
(платных) = активные членства со специализацией.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.access.domain.permissions import SystemRoleKey
from app.modules.staff.domain.models import FutureAppointment, StaffMember
from app.shared.db.enums import AppointmentStatus
from app.shared.db.models.account import Account
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.patient import Patient
from app.shared.db.models.role import Role
from app.shared.db.models.specialization import Specialization


class StaffRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_clinic(self, clinic_id: uuid.UUID) -> Clinic | None:
        return await self._session.get(Clinic, clinic_id)

    async def worker_role_id(self, clinic_id: uuid.UUID) -> uuid.UUID | None:
        return (
            await self._session.execute(
                select(Role.id).where(
                    Role.clinic_id == clinic_id, Role.key == SystemRoleKey.WORKER.value
                )
            )
        ).scalar_one_or_none()

    async def count_seats_used(self, clinic_id: uuid.UUID) -> int:
        """Занято мест: активные сотрудники, кроме владельца — со специальностью и без.

        Тот же счёт, что у подписки (``SubscriptionRepository.seats``) и цены: раньше
        лимит видел только сотрудников со специальностью, и администратор без неё
        занимал бы место бесплатно (решение владельца от 26.09.2026).
        """
        return (
            await self._session.execute(
                select(func.count())
                .select_from(Membership)
                .join(Role, Role.id == Membership.role_id)
                .where(
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                    Role.is_owner.is_(False),
                )
            )
        ).scalar_one()

    async def future_appointments(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID, now: datetime, *, limit: int = 20
    ) -> tuple[int, list[FutureAppointment]]:
        """Будущие записи врача: сколько всего и первые ``limit`` по времени.

        Будущие — ещё не начатые по времени и не закрытые: запланирован,
        подтверждён, пришёл, на приёме. Черновики без даты в календаре не видны
        и сюда не входят.
        """
        conditions = (
            Appointment.clinic_id == clinic_id,
            Appointment.doctor_membership_id == membership_id,
            Appointment.scheduled_at > now,
            Appointment.status.in_(
                (
                    AppointmentStatus.SCHEDULED,
                    AppointmentStatus.CONFIRMED,
                    AppointmentStatus.ARRIVED,
                    AppointmentStatus.IN_PROGRESS,
                )
            ),
        )
        total = (
            await self._session.execute(
                select(func.count()).select_from(Appointment).where(*conditions)
            )
        ).scalar_one()
        if not total:
            return 0, []
        rows = (
            await self._session.execute(
                select(
                    Appointment.id,
                    Appointment.scheduled_at,
                    func.concat_ws(" ", Patient.last_name, Patient.first_name),
                )
                .join(Patient, Patient.id == Appointment.patient_id)
                .where(*conditions)
                .order_by(Appointment.scheduled_at)
                .limit(limit)
            )
        ).all()
        return int(total), [
            FutureAppointment(id=r[0], scheduled_at=r[1], patient_name=(r[2] or "").strip())
            for r in rows
        ]

    async def account_by_phone(self, phone: str) -> Account | None:
        return (
            await self._session.execute(select(Account).where(Account.phone == phone))
        ).scalar_one_or_none()

    async def get_account(self, account_id: uuid.UUID) -> Account | None:
        return await self._session.get(Account, account_id)

    async def specialization_exists(self, specialization_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(Specialization.id).where(Specialization.id == specialization_id)
        )
        return result.scalar_one_or_none() is not None

    async def create_account(self, *, phone: str, full_name: str) -> Account:
        """Создать аккаунт сотрудника — всегда без пароля (вход по коду на телефон)."""
        account = Account(
            phone=phone,
            full_name=full_name,
            password_hash=None,
            must_change_password=False,
        )
        self._session.add(account)
        await self._session.flush()
        return account

    async def create_membership(
        self,
        *,
        clinic_id: uuid.UUID,
        account_id: uuid.UUID,
        role_id: uuid.UUID,
        specialization_id: uuid.UUID | None,
    ) -> Membership:
        membership = Membership(
            clinic_id=clinic_id,
            account_id=account_id,
            role_id=role_id,
            specialization_id=specialization_id,
        )
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def get_membership(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID
    ) -> Membership | None:
        return (
            await self._session.execute(
                select(Membership).where(
                    Membership.id == membership_id, Membership.clinic_id == clinic_id
                )
            )
        ).scalar_one_or_none()

    async def get_role(self, role_id: uuid.UUID) -> Role | None:
        return await self._session.get(Role, role_id)

    async def role_in_clinic(self, clinic_id: uuid.UUID, role_id: uuid.UUID) -> Role | None:
        """Роль ЭТОЙ клиники по id — назначить чужую роль нельзя (IDOR)."""
        return (
            await self._session.execute(
                select(Role).where(Role.id == role_id, Role.clinic_id == clinic_id)
            )
        ).scalar_one_or_none()

    async def list_members(self, clinic_id: uuid.UUID) -> list[StaffMember]:
        rows = (
            await self._session.execute(
                select(
                    Membership.id,
                    Membership.account_id,
                    Account.full_name,
                    Account.phone,
                    Membership.role_id,
                    Role.name,
                    Role.is_owner,
                    Membership.specialization_id,
                    Membership.active,
                    Account.blocked_at,
                    Membership.created_at,
                )
                .join(Account, Account.id == Membership.account_id)
                .join(Role, Role.id == Membership.role_id)
                .where(Membership.clinic_id == clinic_id)
                .order_by(Role.is_owner.desc(), Membership.created_at)
            )
        ).all()
        return [
            StaffMember(
                membership_id=r[0],
                account_id=r[1],
                full_name=r[2],
                phone=r[3],
                role_id=r[4],
                role_name=r[5],
                is_owner=r[6],
                specialization_id=r[7],
                active=r[8],
                blocked_at=r[9],
                created_at=r[10],
            )
            for r in rows
        ]
