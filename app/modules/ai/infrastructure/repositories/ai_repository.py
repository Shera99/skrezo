"""Репозиторий контура ассистента: глоссарий, база знаний, журнал, эскалации."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import AiMessageRole, GlossaryStatus
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_tool_call import AiToolCall
from app.shared.db.models.glossary_term import GlossaryTerm
from app.shared.db.models.kb_document import KbDocument


class AiRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Глоссарий ───────────────────────────────────────────────────────────

    async def approved_glossary(self, clinic_id: uuid.UUID) -> list[tuple[str, str]]:
        """Термины для промпта: одобренные клиники + общие платформы."""
        rows = (
            await self._session.execute(
                select(GlossaryTerm.phrase, GlossaryTerm.meaning)
                .where(
                    GlossaryTerm.status == GlossaryStatus.APPROVED,
                    or_(
                        GlossaryTerm.clinic_id == clinic_id,
                        GlossaryTerm.clinic_id.is_(None),
                    ),
                )
                .order_by(GlossaryTerm.phrase)
            )
        ).all()
        return [(r[0], r[1]) for r in rows]

    async def list_glossary(
        self, clinic_id: uuid.UUID, status: GlossaryStatus | None
    ) -> Sequence[GlossaryTerm]:
        stmt = select(GlossaryTerm).where(
            or_(GlossaryTerm.clinic_id == clinic_id, GlossaryTerm.clinic_id.is_(None))
        )
        if status is not None:
            stmt = stmt.where(GlossaryTerm.status == status)
        return list(
            (await self._session.execute(stmt.order_by(GlossaryTerm.created_at.desc())))
            .scalars()
            .all()
        )

    async def glossary_term(
        self, clinic_id: uuid.UUID, term_id: uuid.UUID
    ) -> GlossaryTerm | None:
        """Термин клиники (общие платформы клиника не правит)."""
        return (
            await self._session.execute(
                select(GlossaryTerm).where(
                    GlossaryTerm.id == term_id, GlossaryTerm.clinic_id == clinic_id
                )
            )
        ).scalar_one_or_none()

    async def suggest_term(
        self, clinic_id: uuid.UUID, phrase: str, meaning: str, lang: str | None
    ) -> bool:
        """Предложение бота в очередь (pending); дубль фразы — молча пропустить."""
        phrase = phrase.strip()
        if not phrase:
            return False
        exists = (
            await self._session.execute(
                select(GlossaryTerm.id).where(
                    or_(
                        GlossaryTerm.clinic_id == clinic_id,
                        GlossaryTerm.clinic_id.is_(None),
                    ),
                    GlossaryTerm.phrase.ilike(phrase),
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            return False
        self._session.add(
            GlossaryTerm(
                clinic_id=clinic_id,
                phrase=phrase,
                meaning=meaning.strip(),
                lang=lang,
                source="assistant",
                status=GlossaryStatus.PENDING,
            )
        )
        await self._session.flush()
        return True

    # ── База знаний ─────────────────────────────────────────────────────────

    async def kb_for_prompt(
        self, clinic_id: uuid.UUID, lang: str, *, max_chars: int = 20_000
    ) -> list[tuple[str, str]]:
        """Активные документы на языке диалога, суммарно не длиннее потолка.

        Потолок — страховка кэшируемого префикса (spec_1: полный контекст до
        30 000 токенов; счётчик и включение поиска — следующий инкремент).
        """
        rows = (
            await self._session.execute(
                select(KbDocument.title, KbDocument.body)
                .where(
                    KbDocument.clinic_id == clinic_id,
                    KbDocument.lang == lang,
                    KbDocument.is_active.is_(True),
                )
                .order_by(KbDocument.created_at)
            )
        ).all()
        docs: list[tuple[str, str]] = []
        used = 0
        for title, body in rows:
            used += len(body)
            if used > max_chars:
                break
            docs.append((title, body))
        return docs

    # ── Журнал инструментов и эскалации ─────────────────────────────────────

    def record_tool_call(
        self,
        conversation_id: uuid.UUID,
        clinic_id: uuid.UUID,
        *,
        tool: str,
        args: dict[str, Any],
        result: dict[str, Any] | list[Any] | None,
        error: str | None,
        duration_ms: int,
    ) -> AiToolCall:
        row = AiToolCall(
            conversation_id=conversation_id,
            clinic_id=clinic_id,
            tool=tool,
            args=args,
            result=result,
            error=error,
            duration_ms=duration_ms,
        )
        self._session.add(row)
        return row

    def add_escalation(
        self,
        conversation_id: uuid.UUID,
        clinic_id: uuid.UUID,
        *,
        reason: str,
        payload: dict[str, Any] | None,
    ) -> AiEscalation:
        row = AiEscalation(
            conversation_id=conversation_id,
            clinic_id=clinic_id,
            reason=reason,
            payload=payload,
        )
        self._session.add(row)
        return row

    async def bot_replies_count(self, conversation_id: uuid.UUID) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(AiMessage)
                .where(
                    AiMessage.conversation_id == conversation_id,
                    AiMessage.role == AiMessageRole.BOT,
                )
            )
        ).scalar_one()
