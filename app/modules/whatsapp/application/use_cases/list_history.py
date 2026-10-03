"""Use case: архив переписки диалога из приложения WhatsApp Business."""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.models.whatsapp_history_message import WhatsAppHistoryMessage


class ListHistoryUseCase:
    def __init__(self, repository: WhatsAppRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        clinic_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        before: int | None,
        limit: int,
    ) -> list[WhatsAppHistoryMessage]:
        # Изоляция клиник: чужой диалог неотличим от несуществующего.
        if not await self._repository.conversation_exists(conversation_id, clinic_id):
            raise NotFoundError("Диалог не найден", code="conversation_not_found")
        return await self._repository.history_page(conversation_id, before=before, limit=limit)
