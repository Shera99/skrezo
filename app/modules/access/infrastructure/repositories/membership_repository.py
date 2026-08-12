"""Репозиторий членств для RBAC: живая роль сотрудника в клинике.

Отдельный от одноимённого репозитория ``identity``: тому нужен полный контекст
членства (название клиники, имя роли) для экрана выбора клиники, а здесь на
каждый защищённый запрос достаточно одного идентификатора роли.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.membership import Membership


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_role_id(
        self, account_id: uuid.UUID, clinic_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Роль активного членства или ``None``, если сотрудник в клинике не работает."""
        return (
            await self._session.execute(
                select(Membership.role_id).where(
                    Membership.account_id == account_id,
                    Membership.clinic_id == clinic_id,
                    Membership.active.is_(True),
                )
            )
        ).scalar_one_or_none()
