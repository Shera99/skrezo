"""Роут: GET /clinic/conversations — диалоги WhatsApp (conversations.manage)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.whatsapp.application.use_cases.list_conversations import (
    ListConversationsUseCase,
)
from app.modules.whatsapp.presentation.deps import get_list_conversations_use_case
from app.modules.whatsapp.presentation.schemas import (
    ConversationListItem,
    ConversationsResponse,
)
from app.shared.auth import ClientContext
from app.shared.db.enums import AiConversationStatus

router = APIRouter()


@router.get(
    "/clinic/conversations",
    response_model=ConversationsResponse,
    summary="Диалоги WhatsApp клиники",
    response_description="Страница диалогов, свежие сверху",
)
async def list_conversations(
    status: AiConversationStatus | None = Query(
        default=None, description="Фильтр по статусу (`active` / `closed` / `escalated`)"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    context: ClientContext = Depends(require_permission("conversations.manage")),
    use_case: ListConversationsUseCase = Depends(get_list_conversations_use_case),
) -> ConversationsResponse:
    """Список переписок с пациентами: шапка, превью и состояние 24-часового окна.

    `windowOpen: false` — сутки с последнего сообщения пациента прошли, поле
    ввода в интерфейсе подменяется списком одобренных шаблонов.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    rows, total = await use_case.execute(
        context.clinic_id, status=status, page=page, page_size=page_size
    )
    now = datetime.now(UTC)
    return ConversationsResponse(
        list=[ConversationListItem.from_row(r, now=now) for r in rows],
        current_page=page,
        page_size=page_size,
        total_count=total,
    )
