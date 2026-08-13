"""Репозиторий членств: активные членства аккаунта с контекстом клиники/роли."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.domain.models import MembershipContext
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.membership import Membership
from app.shared.db.models.role import Role


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _base_query(self) -> Select[Any]:
        return (
            select(
                Membership.id,
                Membership.clinic_id,
                Clinic.name,
                Clinic.type,
                Membership.role_id,
                Role.name,
                Role.key,
                Role.is_owner,
            )
            .join(Clinic, Clinic.id == Membership.clinic_id)
            .join(Role, Role.id == Membership.role_id)
            .where(Membership.active.is_(True))
        )

    @staticmethod
    def _to_context(row: Any) -> MembershipContext:
        return MembershipContext(
            membership_id=row[0],
            clinic_id=row[1],
            clinic_name=row[2],
            clinic_type=row[3],
            role_id=row[4],
            role_name=row[5],
            role_key=row[6],
            is_owner=row[7],
        )

    async def list_active_context(self, account_id: uuid.UUID) -> list[MembershipContext]:
        stmt = self._base_query().where(Membership.account_id == account_id).order_by(Clinic.name)
        rows = (await self._session.execute(stmt)).all()
        return [self._to_context(row) for row in rows]

    async def get_active_context(
        self, account_id: uuid.UUID, clinic_id: uuid.UUID
    ) -> MembershipContext | None:
        stmt = self._base_query().where(
            Membership.account_id == account_id, Membership.clinic_id == clinic_id
        )
        row = (await self._session.execute(stmt)).first()
        return self._to_context(row) if row is not None else None
