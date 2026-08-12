"""Use-case: список аккаунтов платформы с фильтрами и offset-пагинацией."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.modules.accounts.domain.models import AccountRow
from app.modules.accounts.infrastructure.repositories.account_repository import AccountRepository


@dataclass(slots=True)
class ListAccountsResult:
    items: list[AccountRow]
    total_count: int
    current_page: int
    page_size: int


class ListAccountsUseCase:
    def __init__(self, accounts: AccountRepository) -> None:
        self._accounts = accounts

    async def execute(
        self,
        *,
        query: str | None = None,
        clinic_id: uuid.UUID | None = None,
        active: bool | None = None,
        is_platform_admin: bool | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> ListAccountsResult:
        items, total = await self._accounts.list_accounts(
            query=query,
            clinic_id=clinic_id,
            active=active,
            is_platform_admin=is_platform_admin,
            page=page,
            page_size=page_size,
        )
        return ListAccountsResult(
            items=items, total_count=total, current_page=page, page_size=page_size
        )
