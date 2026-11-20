"""Use-case: подключения WhatsApp всех клиник — список для команды Skrezo.

Клиника, подключившая номер сама через окно Meta, видна в кабинете Meta только
как WABA, которой с Skrezo поделились; здесь — вместе с названием клиники,
способом подключения и тем, работает ли номер у нас сейчас.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.whatsapp.domain.models import ConnectionRow, ConnectionSummary
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)


@dataclass(slots=True)
class ListConnectionsResult:
    """Страница подключений, числа для вкладок и параметры пагинации."""

    items: list[ConnectionRow]
    summary: ConnectionSummary
    total_count: int
    current_page: int
    page_size: int


class ListWhatsAppConnectionsUseCase:
    def __init__(self, repository: WhatsAppRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        *,
        connected_via: str | None,
        state: str | None,
        query: str | None,
        page: int,
        page_size: int,
    ) -> ListConnectionsResult:
        """Отфильтровать и отдать страницу; числа вкладок считаются без фильтров."""
        items, total = await self._repository.list_connections(
            connected_via=connected_via,
            state=state,
            query=query,
            page=page,
            page_size=page_size,
        )
        summary = await self._repository.connections_summary()
        return ListConnectionsResult(
            items=items,
            summary=summary,
            total_count=total,
            current_page=page,
            page_size=page_size,
        )
