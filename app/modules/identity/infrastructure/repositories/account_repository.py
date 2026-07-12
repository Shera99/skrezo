"""Репозиторий аккаунтов (клиентский контур)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.account import Account


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_phone(self, phone: str) -> Account | None:
        return (
            await self._session.execute(select(Account).where(Account.phone == phone))
        ).scalar_one_or_none()

    async def get_by_id(self, account_id: uuid.UUID) -> Account | None:
        return await self._session.get(Account, account_id)
