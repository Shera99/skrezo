"""Репозиторий членств (провижининг): создание."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.membership import Membership


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        clinic_id: uuid.UUID,
        account_id: uuid.UUID,
        role_id: uuid.UUID,
        specialization_id: uuid.UUID | None = None,
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
