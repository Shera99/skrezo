"""Репозиторий контура ассистента: глоссарий, база знаний, журнал, эскалации."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ai.domain.knowledge import KB_PROMPT_MAX_CHARS, fits_in_prompt
from app.shared.db.enums import AiConversationStatus, AiMessageRole, AiMode, GlossaryStatus
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_tool_call import AiToolCall
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.glossary_term import GlossaryTerm
from app.shared.db.models.kb_document import KbDocument

#: Причины эскалации, после которых диалог НЕ возвращается боту сам:
#: сотрудник сказал «отвечаю сам», у пациента неотложное или сильная боль без
#: свободного времени — нужен человек; поток сообщений (§14) — бот уже
#: извинился один раз, и возврат означал бы новое извинение каждые 15 минут.
#: Передачи, которые автовозврат не снимает: человек решил вести сам, здоровье,
#: поток сообщений и постоянный отказ WhatsApp (``delivery_failed``) — там бот
#: всё равно не дозвонится до пациента, пока причину не исправят.
STICKY_ESCALATIONS = (
    "manual_takeover",
    "urgent_medical",
    "acute_pain",
    "rate_limited",
    "delivery_failed",
)


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

    async def glossary_term(self, clinic_id: uuid.UUID, term_id: uuid.UUID) -> GlossaryTerm | None:
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
        self, clinic_id: uuid.UUID, lang: str, *, max_chars: int = KB_PROMPT_MAX_CHARS
    ) -> list[tuple[str, str]]:
        """Активные документы на языке диалога, суммарно не длиннее потолка.

        Потолок — тот же, что показывает редактор базы знаний (30 000 токенов ≈
        90 000 символов): что не поместилось, помечено там «не в промпте».
        Подбор нужных документов вместо всех — когда клиника упрётся в порог.
        """
        rows = (
            await self._session.execute(
                select(KbDocument.title, KbDocument.body)
                .where(
                    KbDocument.clinic_id == clinic_id,
                    KbDocument.lang == lang,
                    KbDocument.is_active.is_(True),
                )
                .order_by(KbDocument.created_at, KbDocument.id)
            )
        ).all()
        flags = fits_in_prompt(
            (len(title) + len(body) for title, body in rows), max_chars=max_chars
        )
        return [(title, body) for (title, body), ok in zip(rows, flags, strict=True) if ok]

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

    async def answered_after(self, conversation_id: uuid.UUID, message_id: int) -> bool:
        """Есть ли наш ход (бот, человек, служебный) после сообщения ``message_id``.

        Ф8: задание ответа могло прийти повторно или с опозданием — если диалог
        уже получил ответ на это сообщение, второго быть не должно.
        """
        found = (
            await self._session.execute(
                select(AiMessage.id)
                .where(
                    AiMessage.conversation_id == conversation_id,
                    AiMessage.id > message_id,
                    AiMessage.role != AiMessageRole.PATIENT,
                )
                .limit(1)
            )
        ).first()
        return found is not None

    # ── Черновики режима «Проверяю» (Ф5) ────────────────────────────────────

    async def supersede_pending_drafts(self, conversation_id: uuid.UUID) -> int:
        """Нерассмотренные черновики диалога → ``superseded`` (пришёл свежий)."""
        result = await self._session.execute(
            update(AiMessage)
            .where(AiMessage.conversation_id == conversation_id, AiMessage.review == "pending")
            .values(review="superseded")
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def pending_draft(
        self, clinic_id: uuid.UUID, conversation_id: uuid.UUID, message_id: int
    ) -> AiMessage | None:
        """Черновик на решении — строго своей клиники, ролью бота и ещё ``pending``."""
        return (
            await self._session.execute(
                select(AiMessage).where(
                    AiMessage.id == message_id,
                    AiMessage.conversation_id == conversation_id,
                    AiMessage.clinic_id == clinic_id,
                    AiMessage.role == AiMessageRole.BOT,
                    AiMessage.review == "pending",
                )
            )
        ).scalar_one_or_none()

    async def patient_wrote_after(self, conversation_id: uuid.UUID, message_id: int) -> bool:
        """Пациент написал после черновика — отправлять устаревший ответ нельзя."""
        found = (
            await self._session.execute(
                select(AiMessage.id)
                .where(
                    AiMessage.conversation_id == conversation_id,
                    AiMessage.id > message_id,
                    AiMessage.role == AiMessageRole.PATIENT,
                )
                .limit(1)
            )
        ).first()
        return found is not None

    # ── Эскалации: разрешение (Ф5) ──────────────────────────────────────────

    async def resolve_escalations(
        self, conversation_id: uuid.UUID, *, actor_id: uuid.UUID | None, now: datetime
    ) -> int:
        """Закрыть все открытые эскалации диалога; вернуть число закрытых.

        ``actor_id=None`` — закрыла система (автовозврат боту по таймауту).
        """
        result = await self._session.execute(
            update(AiEscalation)
            .where(
                AiEscalation.conversation_id == conversation_id,
                AiEscalation.resolved_at.is_(None),
            )
            .values(resolved_by=actor_id, resolved_at=now)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def latest_open_escalation_reason(self, conversation_id: uuid.UUID) -> str | None:
        """Причина последней открытой эскалации диалога; ``None`` — открытых нет.

        Именно последней: диалог на номер постоянный, и незакрытая эскалация
        прошлого эпизода не говорит ничего о том, у кого диалог сейчас.
        """
        reason: str | None = (
            await self._session.execute(
                select(AiEscalation.reason)
                .where(
                    AiEscalation.conversation_id == conversation_id,
                    AiEscalation.resolved_at.is_(None),
                )
                .order_by(AiEscalation.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return reason

    async def escalations_to_return(
        self,
        *,
        cutoff: datetime,
        waiting_since: datetime,
        conversation_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Диалоги у администратора, которые пора вернуть боту (решение 12.10.2026).

        Решает **последняя** открытая эскалация диалога: незакрытые эскалации
        прошлых эпизодов (диалог на номер один и постоянный) не должны навсегда
        выключать возврат. Условия — все сразу:

        * диалог ``escalated``, ассистент клиники не выключен;
        * последняя открытая эскалация не «липкая» (:data:`STICKY_ESCALATIONS`);
        * пациент **написал после неё**, и его первое такое сообщение не новее
          ``cutoff``: 15 минут дают сотруднику с момента, когда пациент снова
          ждёт, а не с момента передачи. Решение 15.10.2026 — раньше пациент,
          написавший через час после передачи, через минуту снова попадал к
          боту, и администратор не успевал ответить. Без нового сообщения
          возвращать нечего: бот снова взялся бы за то, что уже передал
          человеку (фото, ошибка отправки), и эскалировал бы его по кругу;
        * последнее сообщение пациента не раньше ``waiting_since`` (окно 24 ч);
        * сотрудник после неё ничего не написал — разговор ведёт человек.

        ``conversation_id`` — перепроверка одного диалога уже под блокировкой.
        """
        latest = (
            select(
                AiEscalation.conversation_id.label("conversation_id"),
                AiEscalation.reason.label("reason"),
                AiEscalation.created_at.label("created_at"),
            )
            .where(AiEscalation.resolved_at.is_(None))
            .distinct(AiEscalation.conversation_id)
            .order_by(AiEscalation.conversation_id, AiEscalation.created_at.desc())
            .subquery()
        )
        waited = (
            select(AiMessage.id)
            .where(
                AiMessage.conversation_id == AiConversation.id,
                AiMessage.role == AiMessageRole.PATIENT,
                AiMessage.created_at > latest.c.created_at,
                AiMessage.created_at <= cutoff,
            )
            .exists()
        )
        staff_replied = (
            select(AiMessage.id)
            .where(
                AiMessage.conversation_id == AiConversation.id,
                AiMessage.role == AiMessageRole.ADMIN,
                AiMessage.created_at >= latest.c.created_at,
            )
            .exists()
        )
        stmt = (
            select(AiConversation.id)
            .join(latest, latest.c.conversation_id == AiConversation.id)
            .join(Clinic, Clinic.id == AiConversation.clinic_id)
            .where(
                AiConversation.status == AiConversationStatus.ESCALATED,
                Clinic.ai_mode != AiMode.OFF,
                latest.c.reason.not_in(STICKY_ESCALATIONS),
                waited,
                AiConversation.last_patient_msg_at >= waiting_since,
                ~staff_replied,
            )
            # Один порядок блокировок у всех прогонов — без взаимных блокировок.
            .order_by(AiConversation.id)
        )
        if conversation_id is not None:
            stmt = stmt.where(AiConversation.id == conversation_id)
        return list((await self._session.execute(stmt)).scalars())
