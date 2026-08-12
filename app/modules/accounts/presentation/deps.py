"""Зависимости раздела «Аккаунты» (платформенный контур)."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.accounts.application.use_cases.list_accounts import ListAccountsUseCase
from app.modules.accounts.infrastructure.repositories.account_repository import AccountRepository


def get_account_repository(session: AsyncSession = Depends(get_session)) -> AccountRepository:
    return AccountRepository(session)


def get_list_accounts_use_case(
    accounts: AccountRepository = Depends(get_account_repository),
) -> ListAccountsUseCase:
    return ListAccountsUseCase(accounts)
