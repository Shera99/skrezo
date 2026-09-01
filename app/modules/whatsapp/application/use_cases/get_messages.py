"""Use-case: история диалога WhatsApp (страница «старше id»)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.errors import NotFoundError
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage


@dataclass(frozen=True, slots=True)
class MessagesResult:
    conversation: AiConversation
    patient_name: str | None
    items: list[AiMessage]


class GetMessagesUseCase:
    def __init__(self, repository: WhatsAppRepository) -> None:
        self._repository = repository

    async def execute(
        self,
        clinic_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        before_id: int | None,
        limit: int,
    ) -> MessagesResult:
        conversation = await self._repository.conversation(clinic_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Диалог не найден", code="conversation_not_found")
        items = await self._repository.messages_page(
            conversation.id, before_id=before_id, limit=limit
        )
        patient_name = await self._repository.patient_name(conversation.patient_id)
        return MessagesResult(conversation=conversation, patient_name=patient_name, items=items)
