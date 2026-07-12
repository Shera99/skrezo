"""Use-case: счётчики клиентов по статусам подписки (для дропдауна фильтра)."""

from __future__ import annotations

from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository
from app.shared.db.enums import SubscriptionStatus


class StatusCountsUseCase:
    def __init__(self, clients: ClientRepository) -> None:
        self._clients = clients

    async def execute(self) -> dict[SubscriptionStatus, int]:
        return await self._clients.status_counts()
