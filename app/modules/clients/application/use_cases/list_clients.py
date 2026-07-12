"""Use-case: список клиентов с фильтрами и offset-пагинацией."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.modules.clients.domain.models import ClientListRow
from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository
from app.shared.db.enums import ClinicType, Lang, SubscriptionStatus


@dataclass(slots=True)
class ListClientsResult:
    items: list[ClientListRow]
    total_count: int
    current_page: int
    page_size: int


class ListClientsUseCase:
    def __init__(self, clients: ClientRepository) -> None:
        self._clients = clients

    async def execute(
        self,
        *,
        lang: Lang,
        type_: ClinicType | None,
        status: SubscriptionStatus | None,
        city_ids: list[uuid.UUID] | None,
        tariff_names: list[str] | None,
        query: str | None,
        page: int,
        page_size: int,
    ) -> ListClientsResult:
        items, total = await self._clients.list_clients(
            lang=lang,
            type_=type_,
            status=status,
            city_ids=city_ids,
            tariff_names=tariff_names,
            query=query,
            page=page,
            page_size=page_size,
        )
        return ListClientsResult(
            items=items, total_count=total, current_page=page, page_size=page_size
        )
