"""Роут: GET /clinic/conversations/{id}/messages — история диалога."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.whatsapp.application.use_cases.get_messages import GetMessagesUseCase
from app.modules.whatsapp.presentation.deps import get_get_messages_use_case
from app.modules.whatsapp.presentation.schemas import MessagesResponse
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Диалог не найден (или принадлежит другой клинике)",
        "content": {
            "application/json": {
                "example": {"error": "conversation_not_found", "message": "Диалог не найден"}
            }
        },
    },
}


@router.get(
    "/clinic/conversations/{conversation_id}/messages",
    response_model=MessagesResponse,
    summary="История диалога WhatsApp",
    response_description="Страница сообщений, новые сверху",
    responses=_ERRORS,
)
async def get_messages(
    conversation_id: uuid.UUID,
    before_id: int | None = Query(
        default=None,
        alias="beforeId",
        description="Курсор «старше этого id» — прокрутка истории вверх",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    context: ClientContext = Depends(require_permission("conversations.manage")),
    use_case: GetMessagesUseCase = Depends(get_get_messages_use_case),
) -> MessagesResponse:
    """Сообщения диалога новыми сверху; продолжение — `beforeId` = id последнего."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    result = await use_case.execute(
        context.clinic_id, conversation_id, before_id=before_id, limit=limit
    )
    return MessagesResponse.build(result.conversation, result.patient_name, result.items)
