"""Use-case: ответить пациенту свободным текстом (в открытое окно)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError, ValidationAppError
from app.modules.whatsapp.domain.window import is_window_open
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import AiMessageRole
from app.shared.db.models.ai_message import AiMessage
from app.shared.realtime import publish_realtime
from app.shared.whatsapp_cloud import WhatsAppCloudClient, WhatsAppSendError


class SendMessageUseCase:
    def __init__(
        self,
        session: AsyncSession,
        repository: WhatsAppRepository,
        client: WhatsAppCloudClient,
    ) -> None:
        self._session = session
        self._repository = repository
        self._client = client

    async def execute(
        self, clinic_id: uuid.UUID, conversation_id: uuid.UUID, body: str
    ) -> AiMessage:
        """Отправить текст в диалог и записать его в историю.

        Порядок именно такой: сначала Graph API, потом строка в БД. Упавшая
        отправка не оставляет в истории «сказанного», которого пациент не
        получал; упавшая запись при доставленном сообщении — меньшее зло, и
        Coexistence-эхо всё равно доложит его повторно.
        """
        conversation = await self._repository.conversation(clinic_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Диалог не найден", code="conversation_not_found")
        binding = await self._repository.binding_by_clinic(clinic_id)
        if binding is None or not binding.active:
            raise ConflictError("WhatsApp не подключён к клинике", code="whatsapp_not_connected")
        now = datetime.now(UTC)
        if not is_window_open(conversation.last_patient_msg_at, now):
            # Правило Meta: сутки после последнего сообщения пациента. Дальше
            # свободный текст не доставится — только одобренный шаблон.
            raise ValidationAppError(
                "Окно 24 часов закрыто: доступны только одобренные шаблоны",
                code="window_closed",
            )
        try:
            sent = await self._client.send_text(
                binding.phone_number_id, conversation.wa_phone, body
            )
        except WhatsAppSendError as exc:
            raise AppError(
                "Не удалось отправить сообщение в WhatsApp",
                code="wa_send_failed",
                status_code=502,
            ) from exc
        message = await self._repository.add_message(
            conversation,
            role=AiMessageRole.ADMIN,
            body=body,
            wa_message_id=sent.wa_message_id,
            sent_at=now,
        )
        conversation.last_msg_at = now
        await publish_realtime(
            self._session,
            clinic_id,
            "conversation.message",
            {"conversationId": str(conversation.id), "direction": "out"},
        )
        await self._session.commit()
        return message
