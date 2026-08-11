"""Use-case: смена своего пароля — только для команды Skrezo.

Пароль есть лишь у аккаунтов ``is_platform_admin``: все остальные входят по коду
из SMS, и менять им нечего. Требует текущий пароль и сбрасывает флаг
``must_change_password``.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnauthorizedError
from app.core.security import hash_password, verify_password
from app.modules.identity.infrastructure.repositories.account_repository import AccountRepository


class ChangePasswordUseCase:
    def __init__(self, session: AsyncSession, accounts: AccountRepository) -> None:
        self._session = session
        self._accounts = accounts

    async def execute(
        self, account_id: uuid.UUID, *, current_password: str, new_password: str
    ) -> None:
        account = await self._accounts.get_by_id(account_id)
        if account is None:
            raise NotFoundError("Аккаунт не найден", code="account_not_found")
        if not account.is_platform_admin:
            raise ConflictError("Этот аккаунт входит по коду из SMS", code="use_otp_login")
        if account.password_hash is None or not verify_password(
            current_password, account.password_hash
        ):
            raise UnauthorizedError("Неверный текущий пароль", code="invalid_credentials")

        account.password_hash = hash_password(new_password)
        account.must_change_password = False
        await self._session.commit()
