"""Use-case: блокировка и разблокировка аккаунта (только команда Skrezo).

Единственный способ отобрать доступ человеку целиком. Исключение из клиники
закрывает только её: аккаунт глобальный, и уволенный заходит в любую другую
клинику, где числится. Блокировка не пускает вовсе — ни по коду, ни по паролю.

Себя заблокировать нельзя: в проде это лишает админа доступа к той самой ручке,
которой блокировку снимают, и чинится только скриптом на сервере.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.accounts.infrastructure.repositories.account_repository import AccountRepository
from app.modules.identity.application.services.client_token_service import ClientTokenService
from app.shared.auth import AccountBlockResolver
from app.shared.db.models.account import Account


class BlockAccountUseCase:
    def __init__(
        self,
        session: AsyncSession,
        accounts: AccountRepository,
        blocks: AccountBlockResolver,
        tokens: ClientTokenService,
    ) -> None:
        self._session = session
        self._accounts = accounts
        self._blocks = blocks
        self._tokens = tokens

    async def block(
        self, account_id: uuid.UUID, *, actor_id: uuid.UUID, reason: str | None
    ) -> Account:
        """Заблокировать аккаунт, погасив все его сессии.

        Повторная блокировка уже заблокированного — не ошибка, но и дату не
        переписывает: «когда заблокировали» должно отвечать про первый раз.
        """
        if account_id == actor_id:
            raise ConflictError("Нельзя заблокировать себя", code="cannot_block_self")
        account = await self._get(account_id)
        if account.blocked_at is None:
            account.blocked_at = datetime.now(UTC)
            account.blocked_reason = reason
            await self._session.commit()

        # Только после коммита: не сложилась запись — не должны падать и сессии.
        # Отзыв здесь уместен (в отличие от исключения из клиники): блокировка
        # касается всего аккаунта, и «других клиник», которые жалко, у неё нет.
        await self._tokens.revoke_all(str(account_id))
        await self._blocks.invalidate(account_id)
        return account

    async def unblock(self, account_id: uuid.UUID) -> Account:
        """Снять блокировку. Сессии не возвращаются — входить заново."""
        account = await self._get(account_id)
        if account.blocked_at is not None:
            account.blocked_at = None
            account.blocked_reason = None
            await self._session.commit()
        await self._blocks.invalidate(account_id)
        return account

    async def _get(self, account_id: uuid.UUID) -> Account:
        account = await self._accounts.get_by_id(account_id)
        if account is None:
            raise NotFoundError("Аккаунт не найден", code="account_not_found")
        return account
