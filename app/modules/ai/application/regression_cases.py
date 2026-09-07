"""Кейсы регрессии из выученных ответов (Ф6): откуда берутся и как правятся.

Ничего не придумывается: кейс рождается из того, что клиника уже одобрила
(ответ на пробел, отправленный черновик) или пережила (выдумка, снятая
валидатором; неотложное состояние). Повтор того же вопроса обновляет
ожидание — очередь не превращается в свалку дублей.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.ai.domain.knowledge import MIN_NORMALIZED_LEN, normalize_question
from app.modules.ai.domain.learning import CASE_KINDS, extract_keywords
from app.modules.ai.infrastructure.repositories.regression_repository import RegressionRepository
from app.shared.db.enums import Lang
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_regression_case import AiRegressionCase
from app.shared.db.updates import apply_updates

_UPDATABLE = frozenset({"is_active", "note"})


def conversation_lang(conversation: AiConversation) -> str:
    return "ky" if conversation.lang is Lang.KG else "ru"


class RegressionCaseService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = RegressionRepository(session)

    # ── Автоматические источники (коммит — за вызывающим) ───────────────────

    async def from_knowledge(
        self,
        clinic_id: uuid.UUID,
        *,
        question: str,
        lang: str,
        document_id: uuid.UUID,
        document_body: str,
        gap_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Ответ на пробел: бот обязан отвечать на этот вопрос из документа."""
        return await self._record(
            clinic_id,
            kind="knowledge",
            lang=lang,
            question=question,
            expect={"kbDocumentId": str(document_id), "keywords": extract_keywords(document_body)},
            source="knowledge_gap",
            origin={"gapId": str(gap_id), "kbDocumentId": str(document_id)},
            created_by=actor_id,
        )

    async def from_reply(
        self,
        conversation: AiConversation,
        *,
        question: str,
        reply: str,
        edited: bool,
        message_id: int,
        actor_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Отправленный черновик — эталон ответа (правленый ценнее исходного)."""
        return await self._record(
            conversation.clinic_id,
            kind="reply",
            lang=conversation_lang(conversation),
            question=question,
            expect={"reply": reply[:1000], "keywords": extract_keywords(reply)},
            source="edited_draft" if edited else "approved_draft",
            origin={"conversationId": str(conversation.id), "messageId": message_id},
            created_by=actor_id,
        )

    async def from_incident(
        self,
        conversation: AiConversation,
        *,
        question: str,
        proposed: str,
        codes: list[str],
    ) -> uuid.UUID | None:
        """Валидатор снял выдумку: на этот вопрос бот обязан отказать или опереться на факты."""
        return await self._record(
            conversation.clinic_id,
            kind="refusal",
            lang=conversation_lang(conversation),
            question=question,
            expect={"proposed": proposed[:1000], "codes": codes},
            source="incident",
            origin={"conversationId": str(conversation.id)},
            created_by=None,
        )

    async def from_medical(
        self, conversation: AiConversation, *, question: str, kind: str
    ) -> uuid.UUID | None:
        """Сработавший медфильтр — кейс на то, что он сработает и завтра."""
        return await self._record(
            conversation.clinic_id,
            kind="medical",
            lang=conversation_lang(conversation),
            question=question,
            expect={"medical": kind},
            source="medical",
            origin={"conversationId": str(conversation.id)},
            created_by=None,
        )

    # ── Ручное управление ────────────────────────────────────────────────────

    async def create_manual(
        self,
        clinic_id: uuid.UUID,
        *,
        actor_id: uuid.UUID,
        kind: str,
        lang: str,
        question: str,
        expected_reply: str | None,
        keywords: list[str] | None,
        medical: str | None,
        proposed: str | None,
        note: str | None,
    ) -> AiRegressionCase:
        if kind not in CASE_KINDS:
            raise ValidationAppError(
                f"Вид кейса из словаря: {', '.join(sorted(CASE_KINDS))}", code="case_kind_unknown"
            )
        expect: dict[str, Any]
        if kind == "medical":
            if medical not in {"urgent", "complaint", "none"}:
                raise ValidationAppError(
                    "Для medical нужно ожидание: urgent, complaint или none",
                    code="case_expect_invalid",
                )
            expect = {"medical": medical}
        elif kind == "refusal":
            expect = {"proposed": (proposed or "").strip()[:1000], "codes": []}
        else:
            words = [w.strip() for w in (keywords or []) if w.strip()] or extract_keywords(
                expected_reply or ""
            )
            if not words:
                raise ValidationAppError(
                    "Нужен эталонный ответ или ключевые слова", code="case_expect_invalid"
                )
            expect = {"keywords": words[:12]}
            if expected_reply:
                expect["reply"] = expected_reply.strip()[:1000]
        case_id = await self._record(
            clinic_id,
            kind=kind,
            lang=lang,
            question=question,
            expect=expect,
            source="manual",
            origin={},
            created_by=actor_id,
            note=note,
        )
        if case_id is None:
            raise ValidationAppError("Слишком короткий вопрос", code="case_question_short")
        await self._session.commit()
        case = await self._repository.get_case(clinic_id, case_id)
        assert case is not None
        return case

    async def update(
        self, clinic_id: uuid.UUID, case_id: uuid.UUID, *, fields: dict[str, Any]
    ) -> AiRegressionCase:
        case = await self._get(clinic_id, case_id)
        apply_updates(
            case, fields, updatable=_UPDATABLE, not_null={"is_active"}, entity_name="кейса"
        )
        await self._session.commit()
        return case

    async def delete(self, clinic_id: uuid.UUID, case_id: uuid.UUID) -> None:
        case = await self._get(clinic_id, case_id)
        await self._session.delete(case)
        await self._session.commit()

    async def _get(self, clinic_id: uuid.UUID, case_id: uuid.UUID) -> AiRegressionCase:
        case = await self._repository.get_case(clinic_id, case_id)
        if case is None:
            raise NotFoundError("Кейс не найден", code="case_not_found")
        return case

    async def _record(
        self,
        clinic_id: uuid.UUID,
        *,
        kind: str,
        lang: str,
        question: str,
        expect: dict[str, Any],
        source: str,
        origin: dict[str, Any],
        created_by: uuid.UUID | None,
        note: str | None = None,
    ) -> uuid.UUID | None:
        normalized = normalize_question(question)
        if len(normalized) < MIN_NORMALIZED_LEN:
            return None
        return await self._repository.upsert_case(
            clinic_id,
            kind=kind,
            lang=lang,
            question=question.strip()[:1000],
            normalized=normalized,
            expect=expect,
            source=source,
            origin=origin,
            note=note,
            created_by=created_by,
        )
