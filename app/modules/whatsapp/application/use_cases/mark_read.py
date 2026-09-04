"""Use-case: отметить диалог прочитанным (Ф5).

Отметка общая для клиники: ресепшен один на всех, и «синяя точка» должна
гаснуть у всех сразу, а не у каждого сотрудника отдельно. Сигнал
``conversation.read`` уходит в сокет в той же транзакции — второе устройство
снимает бейдж без перечитывания списка.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.realtime import publish_realtime


class MarkConversationReadUseCase:
    def __init__(self, session: AsyncSession, repository: WhatsAppRepository) -> None:
        self._session = session
        self._repository = repository

    async def execute(
        self, clinic_id: uuid.UUID, conversation_id: uuid.UUID, up_to_message_id: int | None
    ) -> tuple[int | None, int]:
        """Сдвинуть отметку вперёд; вернуть (id отметки, сколько осталось непрочитанного).

        Назад отметка не двигается: два устройства могут подтвердить чтение в
        разном порядке, и позднее подтверждение не должно «возвращать» точку.
        """
        read_id = await self._repository.mark_read(
            conversation_id, clinic_id, up_to_message_id=up_to_message_id
        )
        if read_id is None and not await self._repository.conversation_exists(
            conversation_id, clinic_id
        ):
            raise NotFoundError("Диалог не найден", code="conversation_not_found")
        unread = await self._repository.unread_count(conversation_id)
        await publish_realtime(
            self._session,
            clinic_id,
            "conversation.read",
            {"conversationId": str(conversation_id), "unreadCount": unread},
        )
        await self._session.commit()
        return read_id, unread
