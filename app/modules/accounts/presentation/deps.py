"""Зависимости раздела «Аккаунты» (платформенный контур)."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.accounts.application.use_cases.block_account import BlockAccountUseCase
from app.modules.accounts.application.use_cases.list_accounts import ListAccountsUseCase
from app.modules.accounts.infrastructure.repositories.account_repository import AccountRepository
from app.modules.identity.application.services.client_token_service import ClientTokenService
from app.modules.identity.presentation.deps import get_client_token_service
from app.shared.auth import AccountBlockResolver, get_account_block_resolver


def get_account_repository(session: AsyncSession = Depends(get_session)) -> AccountRepository:
    return AccountRepository(session)


def get_list_accounts_use_case(
    accounts: AccountRepository = Depends(get_account_repository),
) -> ListAccountsUseCase:
    return ListAccountsUseCase(accounts)


def get_block_account_use_case(
    session: AsyncSession = Depends(get_session),
    accounts: AccountRepository = Depends(get_account_repository),
    blocks: AccountBlockResolver = Depends(get_account_block_resolver),
    tokens: ClientTokenService = Depends(get_client_token_service),
) -> BlockAccountUseCase:
    return BlockAccountUseCase(session, accounts, blocks, tokens)
