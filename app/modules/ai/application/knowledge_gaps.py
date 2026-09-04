"""Пробелы знаний (Ф6): фиксация из конвейера и решения администратора.

Контур обучения без дообучения весов: бот не ответил → строка очереди →
администратор отвечает своими словами → документ базы знаний → бот знает со
следующего же хода (``kb_for_prompt`` читает активные документы). Отказ
«не нужно отвечать» тоже хранится — иначе тот же вопрос вернётся в очередь.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.ai.domain.knowledge import (
    MIN_NORMALIZED_LEN,
    mentions_price,
    normalize_question,
)
from app.modules.ai.infrastructure.repositories.knowledge_gap_repository import (
    KnowledgeGapRepository,
)
from app.shared.db.models.ai_knowledge_gap import AiKnowledgeGap
from app.shared.db.models.kb_document import KbDocument

logger = structlog.get_logger(__name__)

#: Сколько диалогов помнить у пробела — чтобы открыть контекст, не больше.
_KEEP_CONVERSATIONS = 5
#: Словарь категорий базы знаний (spec_1 §3.4).
KB_CATEGORIES = frozenset({"logistics", "aftercare", "faq", "policy", "payment"})


class KnowledgeGapService:
    def __init__(self, session: AsyncSession, repository: KnowledgeGapRepository) -> None:
        self._session = session
        self._repository = repository

    # ── Из конвейера ────────────────────────────────────────────────────────

    async def record(
        self,
        *,
        clinic_id: uuid.UUID,
        conversation_id: uuid.UUID,
        question: str,
        lang: str,
        source: str,
        hint: str | None = None,
    ) -> AiKnowledgeGap | None:
        """Зафиксировать вопрос без ответа; похожий открытый — досчитать, не плодить.

        Коммит — за вызывающим: пробел пишется в той же транзакции, что и
        эскалация, которую он объясняет.
        """
        normalized = normalize_question(question)
        if len(normalized) < MIN_NORMALIZED_LEN:
            return None
        now = datetime.now(UTC)
        gap = await self._repository.find_open_similar(clinic_id, normalized)
        if gap is not None:
            gap.ask_count += 1
            gap.last_asked_at = now
            seen = [str(c) for c in (gap.conversation_ids or [])]
            if str(conversation_id) not in seen:
                gap.conversation_ids = [*seen, str(conversation_id)][-_KEEP_CONVERSATIONS:]
            if hint and not gap.hint:
                gap.hint = hint
            logger.info("ai_knowledge_gap_repeated", gap_id=str(gap.id), count=gap.ask_count)
            return gap
        gap = AiKnowledgeGap(
            clinic_id=clinic_id,
            question=question.strip()[:1000],
            normalized=normalized,
            lang=lang,
            status="unknown",
            source=source,
            hint=hint,
            ask_count=1,
            first_asked_at=now,
            last_asked_at=now,
            conversation_ids=[str(conversation_id)],
        )
        self._session.add(gap)
        await self._session.flush()
        logger.info("ai_knowledge_gap_opened", gap_id=str(gap.id), source=source)
        return gap

    # ── Решения администратора ──────────────────────────────────────────────

    async def answer(
        self,
        clinic_id: uuid.UUID,
        gap_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
        answer: str,
        title: str | None,
        category: str,
    ) -> tuple[AiKnowledgeGap, KbDocument]:
        """Ответить своими словами: рождается документ базы знаний, бот знает сразу."""
        gap = await self._open_gap(clinic_id, gap_id)
        if mentions_price(answer):
            raise ValidationAppError(
                "Цены в базу знаний не кладём: бот берёт их из прайса, а документ устареет",
                code="kb_price_forbidden",
            )
        if category not in KB_CATEGORIES:
            raise ValidationAppError(
                f"Категория из словаря: {', '.join(sorted(KB_CATEGORIES))}",
                code="kb_category_unknown",
            )
        document = KbDocument(
            clinic_id=clinic_id,
            title=(title or gap.question).strip()[:200],
            category=category,
            body=answer.strip(),
            lang=gap.lang,
            is_active=True,
        )
        self._session.add(document)
        await self._session.flush()
        now = datetime.now(UTC)
        gap.status = "answered"
        gap.kb_document_id = document.id
        gap.resolved_by = actor_id
        gap.resolved_at = now
        await self._session.commit()
        return gap, document

    async def dismiss(
        self, clinic_id: uuid.UUID, gap_id: uuid.UUID, *, actor_id: uuid.UUID
    ) -> AiKnowledgeGap:
        """«Не нужно отвечать»: вопрос больше не вернётся в очередь."""
        gap = await self._open_gap(clinic_id, gap_id)
        gap.status = "dismissed"
        gap.resolved_by = actor_id
        gap.resolved_at = datetime.now(UTC)
        await self._session.commit()
        return gap

    async def _open_gap(self, clinic_id: uuid.UUID, gap_id: uuid.UUID) -> AiKnowledgeGap:
        gap = await self._repository.get(clinic_id, gap_id)
        if gap is None:
            raise NotFoundError("Пробел знаний не найден", code="knowledge_gap_not_found")
        if gap.status != "unknown":
            raise ConflictError(
                "По этому вопросу решение уже принято", code="knowledge_gap_resolved"
            )
        return gap
