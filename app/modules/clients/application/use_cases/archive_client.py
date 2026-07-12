"""Use-case: архивация клиента (подписка → cancelled, данные не удаляются)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository


class ArchiveClientUseCase:
    def __init__(self, session: AsyncSession, clients: ClientRepository) -> None:
        self._session = session
        self._clients = clients

    async def execute(self, clinic_id: uuid.UUID, reason: str | None) -> None:
        found = await self._clients.archive(clinic_id, reason)
        if not found:
            raise NotFoundError("Клиент не найден", code="client_not_found")
        await self._session.commit()
