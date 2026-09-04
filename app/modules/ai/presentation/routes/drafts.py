"""Роуты кабинета ассистента (Ф5): решение по черновику и кто ведёт диалог."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.ai.application.drafts import DraftService
from app.modules.ai.presentation.deps import get_draft_service
from app.modules.ai.presentation.schemas import (
    AssistantToggleRequest,
    AssistantToggleResponse,
    DraftSendRequest,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.whatsapp.presentation.schemas import MessageOut
from app.shared.auth import ClientContext
from app.shared.db.enums import AiConversationStatus

router = APIRouter()

_DRAFT_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Диалог не найден или принадлежит другой клинике",
        "content": {
            "application/json": {
                "example": {"error": "conversation_not_found", "message": "Диалог не найден"}
            }
        },
    },
    409: {
        "description": "Черновик уже рассмотрен, устарел, либо WhatsApp не подключён",
        "content": {
            "application/json": {
                "examples": {
                    "not_pending": {
                        "value": {
                            "error": "draft_not_pending",
                            "message": "Черновик уже рассмотрен, устарел или не существует",
                        }
                    },
                    "stale": {
                        "value": {
                            "error": "draft_stale",
                            "message": "Пациент написал снова — этот черновик устарел",
                        }
                    },
                }
            }
        },
    },
    422: {
        "description": "Окно 24 часов закрыто — только одобренные шаблоны",
        "content": {
            "application/json": {
                "example": {"error": "window_closed", "message": "Окно 24 часов закрыто"}
            }
        },
    },
    502: {
        "description": "Graph API не принял сообщение",
        "content": {
            "application/json": {
                "example": {"error": "wa_send_failed", "message": "Не удалось отправить"}
            }
        },
    },
}


def _clinic(context: ClientContext) -> uuid.UUID:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    return context.clinic_id


@router.post(
    "/clinic/conversations/{conversation_id}/drafts/{message_id}/send",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageOut,
    summary="Отправить черновик ассистента пациенту",
    response_description="Отправленное сообщение с решением (approved / edited)",
    responses=_DRAFT_ERRORS,
)
async def send_draft(
    conversation_id: uuid.UUID,
    message_id: int,
    body: DraftSendRequest | None = None,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: DraftService = Depends(get_draft_service),
    audit: AuditService = Depends(get_audit_service),
) -> MessageOut:
    """Кнопка «Отправить» в панели «Ассистент предлагает» (режим «Проверяю»).

    С правкой текста — `review = edited`, исходник бота остаётся в
    `originalBody` как материал для обучения. Черновик, после которого пациент
    написал снова, отправить нельзя (409 `draft_stale`): бот готовит свежий.
    Окно 24 часов проверяется так же, как у свободного текста.
    """
    clinic_id = _clinic(context)
    message = await service.send(
        clinic_id,
        conversation_id,
        message_id,
        actor_id=context.account_id,
        body=body.body if body else None,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="conversation.draft_send",
        entity_type="conversation",
        entity_id=conversation_id,
        payload={"messageId": message.id, "edited": message.review == "edited"},
    )
    return MessageOut.from_orm_message(message)


@router.post(
    "/clinic/conversations/{conversation_id}/drafts/{message_id}/discard",
    response_model=MessageOut,
    summary="Отклонить черновик ассистента",
    response_description="Черновик с отметкой discarded",
    responses={404: _DRAFT_ERRORS[404], 409: _DRAFT_ERRORS[409]},
)
async def discard_draft(
    conversation_id: uuid.UUID,
    message_id: int,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: DraftService = Depends(get_draft_service),
    audit: AuditService = Depends(get_audit_service),
) -> MessageOut:
    """Кнопка «Не отправлять»: пациенту ничего не уходит, отказ учитывается в обучении."""
    clinic_id = _clinic(context)
    message = await service.discard(
        clinic_id, conversation_id, message_id, actor_id=context.account_id
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="conversation.draft_discard",
        entity_type="conversation",
        entity_id=conversation_id,
        payload={"messageId": message.id},
    )
    return MessageOut.from_orm_message(message)


@router.post(
    "/clinic/conversations/{conversation_id}/assistant",
    response_model=AssistantToggleResponse,
    summary="Передать диалог человеку или вернуть боту",
    response_description="Статус диалога после переключения",
    responses={404: _DRAFT_ERRORS[404]},
)
async def toggle_assistant(
    conversation_id: uuid.UUID,
    body: AssistantToggleRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: DraftService = Depends(get_draft_service),
    audit: AuditService = Depends(get_audit_service),
) -> AssistantToggleResponse:
    """`active=false` — «дальше отвечаю сам»: бот замолкает (диалог в `escalated`).

    `active=true` — вернуть боту: открытые эскалации закрываются, и если пациент
    ждёт ответа, ассистенту сразу ставится задание — возврат не означает тишину.
    """
    clinic_id = _clinic(context)
    result = await service.toggle(
        clinic_id, conversation_id, active=body.active, actor_id=context.account_id
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="conversation.assistant_toggle",
        entity_type="conversation",
        entity_id=conversation_id,
        payload={"active": body.active},
    )
    return AssistantToggleResponse(
        conversation_id=conversation_id,
        status=result.status.value,
        bot_active=result.status is AiConversationStatus.ACTIVE,
        resolved_escalations=result.resolved_escalations,
    )
