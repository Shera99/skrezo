"""Репозиторий сотрудников клиники (клиентская сторона).

Работники — практики с ролью «работник» и специализацией. Число мест
(платных) = активные членства со специализацией.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.access.domain.permissions import SystemRoleKey
from app.modules.staff.domain.models import StaffMember
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
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

    async def count_practitioners(self, clinic_id: uuid.UUID) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(Membership)
                .where(
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                    Membership.specialization_id.is_not(None),
                )
            )
        ).scalar_one()

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

    async def create_account(
        self,
        *,
        phone: str,
        full_name: str,
        password_hash: str,
        must_change_password: bool = True,
    ) -> Account:
        account = Account(
            phone=phone,
            full_name=full_name,
            password_hash=password_hash,
            must_change_password=must_change_password,
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
                created_at=r[9],
            )
            for r in rows
        ]
