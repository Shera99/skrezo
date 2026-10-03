"""Роут: GET /clinic/conversations/{id}/history — архив из приложения WhatsApp Business."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.whatsapp.application.use_cases.list_history import ListHistoryUseCase
from app.modules.whatsapp.presentation.deps import get_list_history_use_case
from app.modules.whatsapp.presentation.schemas import HistoryMessageOut, HistoryPageResponse
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
    "/clinic/conversations/{conversation_id}/history",
    response_model=HistoryPageResponse,
    summary="Архив переписки из приложения WhatsApp Business",
    response_description="Сообщения до подключения номера, от свежих к старым",
    responses=_ERRORS,
)
async def get_history(
    conversation_id: uuid.UUID,
    before_id: int | None = Query(
        default=None, alias="beforeId", description="`nextBeforeId` предыдущей страницы"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    context: ClientContext = Depends(require_permission("conversations.manage")),
    use_case: ListHistoryUseCase = Depends(get_list_history_use_case),
) -> HistoryPageResponse:
    """Переписка из приложения до подключения номера к Skrezo (Embedded Signup).

    Показывается над живой лентой диалога: когда `GET …/messages` дошёл до
    начала, кабинет листает архив. Архив упорядочен по времени отправки и не
    участвует в непрочитанных и в ответах ассистента. Пустой список — архива нет.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    items = await use_case.execute(
        context.clinic_id, conversation_id, before=before_id, limit=limit
    )
    return HistoryPageResponse(
        items=[HistoryMessageOut.from_model(m) for m in items],
        next_before_id=items[-1].id if len(items) == limit else None,
    )
