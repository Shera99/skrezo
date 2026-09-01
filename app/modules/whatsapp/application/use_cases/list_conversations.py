"""Use-case: список диалогов WhatsApp клиники."""

from __future__ import annotations

import uuid

from app.modules.whatsapp.domain.models import ConversationRow
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import AiConversationStatus


class ListConversationsUseCase:
    def __init__(self, repository: WhatsAppRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        status: AiConversationStatus | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ConversationRow], int]:
        return await self._repository.list_page(
            clinic_id, status=status, page=page, page_size=page_size
        )
