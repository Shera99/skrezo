"""Роуты экрана «Обучение» (Ф6): очередь вопросов без ответа и решения по ним."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.ai.application.knowledge_gaps import KnowledgeGapService
from app.modules.ai.infrastructure.repositories.knowledge_gap_repository import (
    KnowledgeGapRepository,
)
from app.modules.ai.presentation.deps import get_knowledge_gap_repository, get_knowledge_gap_service
from app.modules.ai.presentation.schemas import (
    AnswerKnowledgeGapRequest,
    KnowledgeGapCounts,
    KnowledgeGapOut,
    KnowledgeGapsResponse,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.shared.auth import ClientContext

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Пробел не найден или принадлежит другой клинике",
        "content": {
            "application/json": {
                "example": {
                    "error": "knowledge_gap_not_found",
                    "message": "Пробел знаний не найден",
                }
            }
        },
    },
    409: {
        "description": "Решение по вопросу уже принято",
        "content": {
            "application/json": {
                "example": {
                    "error": "knowledge_gap_resolved",
                    "message": "По этому вопросу решение уже принято",
                }
            }
        },
    },
    422: {
        "description": "В ответе цена (им место в прайсе) или неизвестная категория",
        "content": {
            "application/json": {
                "example": {
                    "error": "kb_price_forbidden",
                    "message": "Цены в базу знаний не кладём",
                }
            }
        },
    },
}


def _clinic(context: ClientContext) -> uuid.UUID:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    return context.clinic_id


@router.get(
    "/clinic/knowledge-gaps",
    response_model=KnowledgeGapsResponse,
    summary="Очередь «Обучение»: вопросы, на которые бот не ответил",
    response_description="Страница пробелов (частые сверху) и числа вкладок",
)
async def list_knowledge_gaps(
    status_: str | None = Query(
        default=None,
        alias="status",
        pattern="^(unknown|answered|dismissed)$",
        description=(
            "Вкладка: `unknown` — не знает, `answered` — знает, `dismissed` — не нужно отвечать"
        ),
    ),
    q: str | None = Query(default=None, max_length=100, description="Поиск по вопросу и подсказке"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    context: ClientContext = Depends(require_permission("conversations.manage")),
    repository: KnowledgeGapRepository = Depends(get_knowledge_gap_repository),
) -> KnowledgeGapsResponse:
    """Каждая строка — один смысл вопроса; похожие формулировки склеены, поэтому
    «спросили ×4» — честное число. Самые частые сверху: их выгоднее закрыть первыми."""
    clinic_id = _clinic(context)
    rows, total = await repository.list_page(
        clinic_id, status=status_, query=(q or "").strip() or None, page=page, page_size=page_size
    )
    counts = await repository.counts(clinic_id)
    return KnowledgeGapsResponse(
        list=[KnowledgeGapOut.from_orm_gap(g) for g in rows],
        counts=KnowledgeGapCounts(**counts),
        current_page=page,
        page_size=page_size,
        total_count=total,
    )


@router.post(
    "/clinic/knowledge-gaps/{gap_id}/answer",
    status_code=status.HTTP_201_CREATED,
    response_model=KnowledgeGapOut,
    summary="Ответить своими словами — бот выучит сразу",
    response_description="Пробел со ссылкой на созданный документ базы знаний",
    responses=_ERRORS,
)
async def answer_knowledge_gap(
    gap_id: uuid.UUID,
    body: AnswerKnowledgeGapRequest,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: KnowledgeGapService = Depends(get_knowledge_gap_service),
    audit: AuditService = Depends(get_audit_service),
) -> KnowledgeGapOut:
    """Ответ становится активным документом базы знаний на языке вопроса и уходит
    в промпт со следующего хода. Цены в ответе запрещены — бот берёт их из прайса."""
    clinic_id = _clinic(context)
    gap, document = await service.answer(
        clinic_id,
        gap_id,
        actor_id=context.account_id,
        answer=body.answer,
        title=body.title,
        category=body.category,
    )
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="knowledge_gap.answer",
        entity_type="knowledge_gap",
        entity_id=gap.id,
        payload={"kbDocumentId": str(document.id)},
    )
    return KnowledgeGapOut.from_orm_gap(gap)


@router.post(
    "/clinic/knowledge-gaps/{gap_id}/dismiss",
    response_model=KnowledgeGapOut,
    summary="«Не нужно отвечать»",
    response_description="Пробел со статусом dismissed",
    responses={404: _ERRORS[404], 409: _ERRORS[409]},
)
async def dismiss_knowledge_gap(
    gap_id: uuid.UUID,
    context: ClientContext = Depends(require_permission("conversations.manage")),
    service: KnowledgeGapService = Depends(get_knowledge_gap_service),
    audit: AuditService = Depends(get_audit_service),
) -> KnowledgeGapOut:
    """Вопрос вычёркивается из очереди и больше в неё не возвращается."""
    clinic_id = _clinic(context)
    gap = await service.dismiss(clinic_id, gap_id, actor_id=context.account_id)
    await audit.record_clinic(
        actor_id=context.account_id,
        clinic_id=clinic_id,
        action="knowledge_gap.dismiss",
        entity_type="knowledge_gap",
        entity_id=gap.id,
        payload=None,
    )
    return KnowledgeGapOut.from_orm_gap(gap)
