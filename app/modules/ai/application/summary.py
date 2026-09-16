"""Резюме закрытого диалога (§3.3) — дешёвый вызов модели раз в сутки.

Смысл: следующий разговор с тем же номером начинается не с чистого листа.
В контекст подкладываются последние пять резюме — «записывалась на чистку,
перенесла на четверг» стоит одного предложения и экономит пациенту пересказ.

Ограничения намеренные:

* **без медицинских подробностей** — пересказ жалоб, пропущенный через
  модель, хранить как факт о пациенте нельзя;
* модель недоступна или диалог пуст — резюме просто не появляется: это не
  повод ронять фоновую задачу, закрывающую диалоги.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.application.services.usage_meter import UsageMeter
from app.shared.ai import AiMessagePart, AiProvider, AiProviderError
from app.shared.db.enums import AiMessageRole, Lang
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_summary import AiSummary

logger = structlog.get_logger(__name__)

#: Резюме короткое по определению — длинное никто не прочитает и в промпт
#: пять таких не влезут без вытеснения полезного контекста.
_MAX_TOKENS = 200
_HISTORY_LIMIT = 40

_SYSTEM = (
    "Ты помощник стоматологической клиники. Составь резюме переписки с "
    "пациентом: 2–3 коротких предложения, {lang_name}. Только суть: зачем "
    "человек писал и чем закончилось (записался, перенёс, отказался, ждёт "
    "ответа). НЕ упоминай симптомы, жалобы, диагнозы и назначения. Не "
    "выдумывай того, чего нет в переписке. Верни только текст резюме, без "
    "пояснений и кавычек."
)


class ConversationSummaryService:
    """Строит и сохраняет резюме одного закрытого диалога."""

    def __init__(self, session: AsyncSession, provider: AiProvider | None) -> None:
        self._session = session
        self._provider = provider

    async def build(self, conversation_id: uuid.UUID) -> AiSummary | None:
        if self._provider is None:
            return None
        conversation = await self._session.get(AiConversation, conversation_id)
        if conversation is None:
            return None
        # Диалог постоянный: резюмируется эпизод — сообщения после прошлого
        # резюме. Нет новых реплик пациента — подытоживать нечего (и повторный
        # прогон задачи второго резюме не плодит).
        since = await self._last_summary_at(conversation_id)
        transcript = await self._transcript(conversation_id, since)
        if not transcript:
            return None

        lang = "ky" if conversation.lang is Lang.KG else "ru"
        lang_name = "на кыргызском" if lang == "ky" else "на русском"
        try:
            result = await self._provider.complete(
                system=_SYSTEM.format(lang_name=lang_name),
                parts=[AiMessagePart(role="user", text=transcript)],
                tools=[],
                temperature=0.0,
                max_tokens=_MAX_TOKENS,
            )
        except AiProviderError as exc:
            logger.warning("ai_summary_failed", error=str(exc))
            return None
        await UsageMeter(self._session).model_call(conversation.clinic_id, result.usage)
        body = (result.text or "").strip()
        if not body:
            return None

        summary = AiSummary(
            clinic_id=conversation.clinic_id,
            conversation_id=conversation.id,
            patient_id=conversation.patient_id,
            wa_phone=conversation.wa_phone,
            body=body,
            lang=lang,
            model=self._provider.model_name,
        )
        self._session.add(summary)
        return summary

    async def _last_summary_at(self, conversation_id: uuid.UUID) -> datetime | None:
        """Момент прошлого резюме диалога — граница текущего эпизода.

        Повторный прогон задачи резюме одного эпизода не плодит: новых
        сообщений после резюме нет — значит, подытожено.
        """
        return (
            await self._session.execute(
                select(func.max(AiSummary.created_at)).where(
                    AiSummary.conversation_id == conversation_id
                )
            )
        ).scalar_one()

    async def _transcript(self, conversation_id: uuid.UUID, since: datetime | None) -> str:
        """Переписка эпизода одной строкой: «пациент: …» / «клиника: …».

        Пустая строка, если пациент в эпизоде не писал: напоминание клиники
        без ответа — не разговор, вызов модели на него тратить незачем.
        """
        stmt = select(AiMessage).where(AiMessage.conversation_id == conversation_id)
        if since is not None:
            stmt = stmt.where(AiMessage.created_at > since)
        rows = (
            await self._session.execute(stmt.order_by(AiMessage.id.desc()).limit(_HISTORY_LIMIT))
        ).scalars()
        messages = list(reversed(list(rows)))
        if not any(message.role is AiMessageRole.PATIENT for message in messages):
            return ""
        lines: list[str] = []
        for message in messages:
            text = (message.transcript or message.body or "").strip()
            if not text:
                continue
            who = "пациент" if message.role is AiMessageRole.PATIENT else "клиника"
            lines.append(f"{who}: {text}")
        return "\n".join(lines)


async def recent_summaries(
    session: AsyncSession, clinic_id: uuid.UUID, wa_phone: str, limit: int = 5
) -> list[str]:
    """Последние резюме по номеру — в контекст нового диалога (§4.1)."""
    rows = (
        await session.execute(
            select(AiSummary.body)
            .where(AiSummary.clinic_id == clinic_id, AiSummary.wa_phone == wa_phone)
            .order_by(AiSummary.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return list(rows)
