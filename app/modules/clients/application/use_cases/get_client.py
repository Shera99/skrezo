"""Use-case: карточка клиента."""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError
from app.modules.clients.domain.models import ClientDetail
from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository
from app.shared.db.enums import Lang


class GetClientUseCase:
    def __init__(self, clients: ClientRepository) -> None:
        self._clients = clients

    async def execute(self, clinic_id: uuid.UUID, *, lang: Lang) -> ClientDetail:
        detail = await self._clients.get_client(clinic_id, lang=lang)
        if detail is None:
            raise NotFoundError("Клиент не найден", code="client_not_found")
        return detail
