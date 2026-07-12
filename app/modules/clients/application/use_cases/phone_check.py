"""Use-case: проверка занятости телефона (свободен/занят кем-то)."""

from __future__ import annotations

from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository
from app.shared.domain.value_objects import normalize_phone


class PhoneCheckUseCase:
    def __init__(self, clients: ClientRepository) -> None:
        self._clients = clients

    async def execute(self, phone_raw: str) -> str | None:
        """Вернуть ФИО владельца номера (занят) или None (свободен)."""
        return await self._clients.owner_name_by_phone(normalize_phone(phone_raw))
