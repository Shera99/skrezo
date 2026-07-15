"""Use-case: сброс пароля администратора клиента (Skrezo-админка).

Самообслуживаемого «забыл пароль» нет (канал доставки кода не выбран), поэтому
сброс инициирует команда Skrezo: владельцу выдаётся новый **временный** пароль
(`must_change_password=true`), который возвращается один раз для передачи.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.security import generate_temp_password, hash_password
from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository


class ResetAdminPasswordUseCase:
    def __init__(self, session: AsyncSession, clients: ClientRepository) -> None:
        self._session = session
        self._clients = clients

    async def execute(self, clinic_id: uuid.UUID) -> str:
        owner = await self._clients.owner_account(clinic_id)
        if owner is None:
            raise NotFoundError("Клиент не найден", code="client_not_found")
        temp_password = generate_temp_password()
        owner.password_hash = hash_password(temp_password)
        owner.must_change_password = True
        await self._session.commit()
        return temp_password
