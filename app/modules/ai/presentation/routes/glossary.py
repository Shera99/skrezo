"""Роуты глоссария: словарь фраз + очередь предложений бота (право conversations.manage)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import ForbiddenError, NotFoundError
from app.modules.access.presentation.deps import require_permission
from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.modules.ai.presentation.deps import get_ai_repository
from app.modules.ai.presentation.schemas import (
    CreateGlossaryTermRequest,
    GlossaryListResponse,
    GlossaryTermOut,
)
from app.shared.auth import ClientContext
from app.shared.db.enums import GlossaryStatus
from app.shared.db.models.glossary_term import GlossaryTerm

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Термин не найден (или это общий словарь платформы)",
        "content": {"application/json": {"example": {"error": "term_not_found"}}},
    },
}


@router.get(
    "/clinic/glossary",
    response_model=GlossaryListResponse,
    summary="Глоссарий клиники (+ общий словарь платформы)",
    response_description="Термины; фильтр по статусу — очередь предложений",
)
async def list_glossary(
    status_filter: GlossaryStatus | None = Query(
        default=None,
        alias="status",
        description="`pending` — очередь предложений бота на одобрение",
    ),
    context: ClientContext = Depends(require_permission("conversations.manage")),
    repository: AiRepository = Depends(get_ai_repository),
) -> GlossaryListResponse:
    """Словарь, которым бот понимает пациентов.

    В промпт попадают только `approved`. `pending` — предложения бота: он
    переспросил пациента, получил подтверждение и положил пару сюда; автозаписи
    в действующий словарь нет намеренно — неверно понятая фраза стала бы
    системной ошибкой для всех следующих пациентов.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    items = await repository.list_glossary(context.clinic_id, status_filter)
    return GlossaryListResponse(list=[GlossaryTermOut.from_orm_term(t) for t in items])


@router.post(
    "/clinic/glossary",
    status_code=status.HTTP_201_CREATED,
    response_model=GlossaryTermOut,
    summary="Добавить термин вручную (сразу approved)",
    response_description="Созданный термин",
)
async def create_term(
    body: CreateGlossaryTermRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    session: AsyncSession = Depends(get_session),
) -> GlossaryTermOut:
    """Термин от администратора действует сразу — человек и есть контроль."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    term = GlossaryTerm(
        clinic_id=context.clinic_id,
        phrase=body.phrase.strip(),
        meaning=body.meaning.strip(),
        lang=body.lang,
        source="manual",
        status=GlossaryStatus.APPROVED,
    )
    session.add(term)
    await session.commit()
    return GlossaryTermOut.from_orm_term(term)


@router.post(
    "/clinic/glossary/{term_id}/approve",
    response_model=GlossaryTermOut,
    summary="Одобрить предложение бота",
    response_description="Термин после одобрения (бот начинает его знать)",
    responses=_NOT_FOUND,
)
async def approve_term(
    term_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    repository: AiRepository = Depends(get_ai_repository),
    session: AsyncSession = Depends(get_session),
) -> GlossaryTermOut:
    """С этого момента фраза попадает в промпт — бот не переспрашивает снова."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    term = await repository.glossary_term(context.clinic_id, term_id)
    if term is None:
        raise NotFoundError("Термин не найден", code="term_not_found")
    term.status = GlossaryStatus.APPROVED
    await session.commit()
    return GlossaryTermOut.from_orm_term(term)


@router.delete(
    "/clinic/glossary/{term_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить термин (или отклонить предложение)",
    response_description="Удалено",
    responses=_NOT_FOUND,
)
async def delete_term(
    term_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    repository: AiRepository = Depends(get_ai_repository),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Общий словарь платформы клиника не правит — только свои термины."""
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    term = await repository.glossary_term(context.clinic_id, term_id)
    if term is None:
        raise NotFoundError("Термин не найден", code="term_not_found")
    await session.delete(term)
    await session.commit()
