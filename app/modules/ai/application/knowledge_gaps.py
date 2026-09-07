"""Пробелы знаний (Ф6): фиксация из конвейера и решения администратора.

Контур обучения без дообучения весов: бот не ответил → строка очереди →
администратор отвечает своими словами → документ базы знаний → бот знает со
следующего же хода (``kb_for_prompt`` читает активные документы). Отказ
«не нужно отвечать» тоже хранится — иначе тот же вопрос вернётся в очередь.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UpstreamError
from app.modules.ai.application.kb import KbService
from app.modules.ai.application.regression_cases import RegressionCaseService
from app.modules.ai.domain.knowledge import (
    KB_CATEGORIES,
    MIN_NORMALIZED_LEN,
    estimate_tokens,
    mentions_price,
    normalize_question,
)
from app.modules.ai.infrastructure.repositories.kb_repository import KbRepository
from app.modules.ai.infrastructure.repositories.knowledge_gap_repository import (
    KnowledgeGapRepository,
)
from app.modules.usage.application.services.usage_meter import UsageMeter
from app.shared.ai import AiMessagePart, AiProvider, AiProviderError
from app.shared.db.models.ai_knowledge_gap import AiKnowledgeGap
from app.shared.db.models.kb_document import KbDocument

logger = structlog.get_logger(__name__)

#: Сколько диалогов помнить у пробела — чтобы открыть контекст, не больше.
_KEEP_CONVERSATIONS = 5

_REWRITE_SYSTEM = (
    "Ты редактор базы знаний стоматологической клиники.\n"
    "Администратор ответил на вопрос пациента своими словами. Преврати ответ в короткий "
    "документ базы знаний {lang_name}.\n"
    "Правила:\n"
    "- пиши нейтрально и коротко (2–6 предложений), от лица клиники, без обращения к "
    "конкретному пациенту;\n"
    "- сохраняй только факты из ответа администратора, ничего не додумывай;\n"
    "- цен и сумм в документе быть не должно: если администратор назвал цену — убери её и "
    "напиши об этом в notes;\n"
    "- никаких диагнозов и медицинских советов сверх сказанного;\n"
    "- category — одно из: logistics (как доехать, парковка, график), aftercare (после "
    "процедуры), faq (общие вопросы), policy (правила: отмена, опоздание), payment (оплата, "
    "рассрочка).\n"
    'Ответь строго JSON без пояснений: {{"title": "…", "body": "…", "category": "…", '
    '"notes": "…"}}'
)
_REWRITE_MAX_TOKENS = 700
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class RewriteProposal:
    """Документ, который модель предлагает на подтверждение — ничего не сохранено."""

    title: str
    body: str
    category: str
    notes: str | None
    mentions_price: bool
    tokens_estimate: int


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
        document = await KbService(self._session, KbRepository(self._session)).create(
            clinic_id,
            actor_id=actor_id,
            title=title or gap.question,
            category=category,
            body=answer,
            lang=gap.lang,
        )
        # Ф6: выученный вопрос — кейс регрессии «бот отвечает из документа».
        await RegressionCaseService(self._session).from_knowledge(
            clinic_id,
            question=gap.question,
            lang=gap.lang,
            document_id=document.id,
            document_body=document.body,
            gap_id=gap.id,
            actor_id=actor_id,
        )
        now = datetime.now(UTC)
        gap.status = "answered"
        gap.kb_document_id = document.id
        gap.resolved_by = actor_id
        gap.resolved_at = now
        await self._session.commit()
        return gap, document

    async def rewrite(
        self,
        clinic_id: uuid.UUID,
        gap_id: uuid.UUID,
        *,
        answer: str,
        provider: AiProvider | None,
    ) -> RewriteProposal:
        """Модель переписывает ответ администратора в документ — на подтверждение.

        Ничего не сохраняется: человек правит и подтверждает через ``answer``.
        Токены вызова учитываются клинике. Модель недоступна → 502
        ``ai_unavailable`` — ответ можно сохранить и как есть.
        """
        gap = await self._open_gap(clinic_id, gap_id)
        if provider is None:
            raise UpstreamError(
                "Модель не настроена — сохраните ответ как есть", code="ai_unavailable"
            )
        lang_name = "на кыргызском языке" if gap.lang == "ky" else "на русском языке"
        try:
            result = await provider.complete(
                system=_REWRITE_SYSTEM.format(lang_name=lang_name),
                parts=[
                    AiMessagePart(
                        role="user",
                        text=(
                            f"Вопрос пациента: {gap.question}\n"
                            f"Ответ администратора: {answer.strip()}"
                        ),
                    )
                ],
                tools=[],
                temperature=0.2,
                max_tokens=_REWRITE_MAX_TOKENS,
            )
        except AiProviderError as exc:
            logger.warning("kb_rewrite_failed", gap_id=str(gap.id), error=str(exc))
            raise UpstreamError(
                "Модель сейчас недоступна — сохраните ответ как есть", code="ai_unavailable"
            ) from exc
        await UsageMeter(self._session).model_call(clinic_id, result.usage)
        await self._session.commit()
        return _parse_proposal(result.text or "", fallback_title=gap.question, fallback_body=answer)

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


def _parse_proposal(raw: str, *, fallback_title: str, fallback_body: str) -> RewriteProposal:
    """JSON модели → предложение; невалидный ответ — ответ администратора как есть."""
    data: dict[str, Any] = {}
    try:
        parsed = json.loads(_FENCE.sub("", raw.strip()))
        if isinstance(parsed, dict):
            data = parsed
    except ValueError:
        logger.warning("kb_rewrite_not_json", head=raw[:80])
    body = str(data.get("body") or "").strip() or fallback_body.strip()
    title = str(data.get("title") or "").strip()[:200] or fallback_title.strip()[:200]
    category = str(data.get("category") or "faq")
    if category not in KB_CATEGORIES:
        category = "faq"
    notes = str(data.get("notes") or "").strip() or None
    if not data:
        notes = "Модель не вернула документ — показан ответ как есть"
    priced = mentions_price(body) or mentions_price(title)
    if priced:
        notes = (
            notes + "; " if notes else ""
        ) + "в тексте осталась цена — уберите перед сохранением"
    return RewriteProposal(
        title=title,
        body=body,
        category=category,
        notes=notes,
        mentions_price=priced,
        tokens_estimate=estimate_tokens(body),
    )
