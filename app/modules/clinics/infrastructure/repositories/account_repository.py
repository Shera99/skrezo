"""Репозиторий аккаунтов (провижининг): дедуп по телефону и создание."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import Lang
from app.shared.db.models.account import Account


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exists_by_phone(self, phone: str) -> bool:
        result = await self._session.execute(select(Account.id).where(Account.phone == phone))
        return result.scalar_one_or_none() is not None

    async def create(
        self,
        *,
        phone: str,
        full_name: str,
        lang: Lang,
        onboarded: bool,
        password_hash: str,
        must_change_password: bool = True,
    ) -> Account:
        account = Account(
            phone=phone,
            full_name=full_name,
            lang=lang,
            onboarded=onboarded,
            password_hash=password_hash,
            must_change_password=must_change_password,
        )
        self._session.add(account)
        await self._session.flush()
        return account
