"""Роут: POST /clinic/conversations/{id}/messages — ответить пациенту."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.whatsapp.application.use_cases.send_message import SendMessageUseCase
from app.modules.whatsapp.presentation.deps import get_send_message_use_case
from app.modules.whatsapp.presentation.schemas import MessageOut, SendMessageRequest
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "Диалог не найден"},
    409: {
        "description": "WhatsApp не подключён к клинике",
        "content": {
            "application/json": {
                "example": {
                    "error": "whatsapp_not_connected",
                    "message": "WhatsApp не подключён к клинике",
                }
            }
        },
    },
    422: {
        "description": "Окно 24 часов закрыто — доступны только шаблоны",
        "content": {
            "application/json": {
                "example": {
                    "error": "window_closed",
                    "message": "Окно 24 часов закрыто: доступны только одобренные шаблоны",
                }
            }
        },
    },
    502: {
        "description": "Graph API отказал или не ответил",
        "content": {
            "application/json": {
                "example": {
                    "error": "wa_send_failed",
                    "message": "Не удалось отправить сообщение в WhatsApp",
                }
            }
        },
    },
}


@router.post(
    "/clinic/conversations/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageOut,
    summary="Ответить пациенту в WhatsApp",
    response_description="Отправленное сообщение",
    responses=_ERRORS,
)
async def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    use_case: SendMessageUseCase = Depends(get_send_message_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> MessageOut:
    """Свободный текст пациенту — только пока открыто 24-часовое окно Meta.

    Окно закрыто (сутки с последнего сообщения пациента) → 422 `window_closed`:
    интерфейс подменяет поле ввода списком одобренных шаблонов.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    message = await use_case.execute(context.clinic_id, conversation_id, body.body)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=context.clinic_id,
        action="conversation.message",
        entity_type="conversation",
        entity_id=conversation_id,
        payload={"messageId": message.id},
    )
    return MessageOut.from_orm_message(message)
