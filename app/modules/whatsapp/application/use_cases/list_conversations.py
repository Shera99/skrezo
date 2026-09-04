"""Use-case: список диалогов WhatsApp клиники и счётчики вкладок (Ф5)."""

from __future__ import annotations

import uuid
from typing import Literal

from app.modules.whatsapp.domain.models import ConversationCounters, ConversationRow
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import AiConversationStatus

#: Вкладки списка: все / ждут ответа / нужен человек.
ConversationFilter = Literal["all", "awaiting", "needs_human"]


class ListConversationsUseCase:
    def __init__(self, repository: WhatsAppRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        status: AiConversationStatus | None,
        filter_: ConversationFilter = "all",
        query: str | None = None,
        page: int,
        page_size: int,
    ) -> tuple[list[ConversationRow], int]:
        return await self._repository.list_page(
            clinic_id,
            status=status,
            filter_=filter_,
            query=(query or "").strip() or None,
            page=page,
            page_size=page_size,
        )

    async def counters(self, clinic_id: uuid.UUID) -> ConversationCounters:
        """Числа на вкладках и бейдж — одним запросом, а не тремя списками."""
        return await self._repository.counters(clinic_id)
