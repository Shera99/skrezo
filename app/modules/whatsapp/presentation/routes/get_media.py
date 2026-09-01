"""Роут: GET /clinic/conversations/{id}/messages/{messageId}/media — вложение."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.core.errors import ForbiddenError, NotFoundError
from app.modules.access.presentation.deps import require_permission
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.modules.whatsapp.presentation.deps import get_whatsapp_repository
from app.shared.auth import ClientContext
from app.shared.storage.files import storage_root

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Сообщение не найдено либо вложение не скачано",
        "content": {"application/json": {"example": {"error": "media_not_found"}}},
    },
}


@router.get(
    "/clinic/conversations/{conversation_id}/messages/{message_id}/media",
    response_class=FileResponse,
    summary="Вложение сообщения (голосовое, снимок, документ)",
    response_description="Файл вложения",
    responses=_ERRORS,
)
async def get_media(
    conversation_id: uuid.UUID,
    message_id: int,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    repository: WhatsAppRepository = Depends(get_whatsapp_repository),
) -> FileResponse:
    """Отдать скачанное вложение; раздачи статикой нет — доступ идёт через право.

    `hasMedia: false` у сообщения означает, что вложение не скачивалось
    (например, канал работал без кредов Meta) — тогда здесь 404.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    message = await repository.message(context.clinic_id, conversation_id, message_id)
    if message is None or message.media_path is None:
        raise NotFoundError("Вложение не найдено", code="media_not_found")
    return FileResponse(
        storage_root() / message.media_path,
        media_type=message.media_mime or "application/octet-stream",
    )
