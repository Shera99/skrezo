"""Роуты кабинета ассистента (Ф5): счётчики вкладок и отметка прочтения."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.whatsapp.application.use_cases.list_conversations import (
    ListConversationsUseCase,
)
from app.modules.whatsapp.application.use_cases.mark_read import MarkConversationReadUseCase
from app.modules.whatsapp.presentation.deps import (
    get_list_conversations_use_case,
    get_mark_read_use_case,
)
from app.modules.whatsapp.presentation.schemas import (
    ConversationCountersResponse,
    MarkReadRequest,
    MarkReadResponse,
)
from app.shared.auth import ClientContext

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Диалог не найден или принадлежит другой клинике",
        "content": {
            "application/json": {
                "example": {"error": "conversation_not_found", "message": "Диалог не найден"}
            }
        },
    },
}


@router.get(
    "/clinic/conversations/counters",
    response_model=ConversationCountersResponse,
    summary="Счётчики вкладок и бейдж раздела",
    response_description="Числа «Все / Ждут ответа / Нужен человек» и непрочитанное",
)
async def conversation_counters(
    context: ClientContext = Depends(require_permission("conversations.manage")),
    use_case: ListConversationsUseCase = Depends(get_list_conversations_use_case),
) -> ConversationCountersResponse:
    """Одна ручка вместо трёх списков: интерфейс рисует вкладки и бейдж по ней.

    `unread` — число диалогов с непрочитанным (бейдж «Ассистент N»), остальные
    — размеры вкладок. Закрытые диалоги в `total` и `awaiting` не входят.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    counters = await use_case.counters(context.clinic_id)
    return ConversationCountersResponse(
        total=counters.total,
        awaiting=counters.awaiting,
        needs_human=counters.needs_human,
        unread=counters.unread,
    )


@router.post(
    "/clinic/conversations/{conversation_id}/read",
    response_model=MarkReadResponse,
    summary="Отметить диалог прочитанным",
    response_description="Отметка прочтения и остаток непрочитанного",
    responses=_NOT_FOUND,
)
async def mark_conversation_read(
    conversation_id: uuid.UUID,
    body: MarkReadRequest | None = None,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    use_case: MarkConversationReadUseCase = Depends(get_mark_read_use_case),
) -> MarkReadResponse:
    """Зовётся при открытии диалога (и при показе новых сообщений в открытом).

    Отметка **общая для клиники**: ресепшен один на всех, и точка гаснет у
    всех сразу. Назад отметка не двигается — два устройства могут подтвердить
    чтение в разном порядке. В сокет уходит `conversation.read`, чтобы бейдж
    упал и на втором устройстве без перечитывания списка.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    read_id, unread = await use_case.execute(
        context.clinic_id, conversation_id, body.up_to_message_id if body else None
    )
    return MarkReadResponse(read_message_id=read_id, unread_count=unread)
