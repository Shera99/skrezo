"""Задачи WhatsApp-канала: разбор вебхука и закрытие остывших диалогов.

``process_wa_inbound`` ставится вебхуком (тот обязан ответить Meta за 5 секунд
и потому не разбирает payload сам). Повторяется при падении: обработка
идемпотентна — сообщения дедуплицируются по ``wa_message_id``.

``close_stale_conversations`` — cron: диалог без сообщений сутки переходит в
``closed``, и по нему строится резюме (§3.3) — контекст следующего разговора
с тем же номером.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.db import session_factory
from app.core.jobs import PROCESS_WA_INBOUND, SUMMARIZE_CONVERSATION
from app.jobs.tasks.ai import build_reply_coordinator, enqueue_ai_reply
from app.jobs.tasks.context import attempt
from app.modules.ai.application.summary import ConversationSummaryService
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.modules.whatsapp.application.services.inbound import InboundProcessor
from app.modules.whatsapp.domain.window import WINDOW
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.ai.factory import build_ai_provider
from app.shared.speech import build_speech_provider
from app.shared.whatsapp_cloud import build_whatsapp_client


async def process_wa_inbound(ctx: dict[str, Any], payload: dict[str, Any]) -> int:
    """Разобрать payload вебхука и поставить ответы ассистента; вернуть число новых.

    Ответ — отдельным заданием на диалог (Ф8), а не здесь же: Meta пакует
    сообщения разных людей в один запрос, и общий таймаут на пачку ронял
    задание посреди ответов, а повтор их уже не находил (дедуп). Без очереди
    (прямой вызов) отвечаем на месте, как раньше.
    """
    async with JobRunRecorder(PROCESS_WA_INBOUND, attempt(ctx)) as run:
        async with session_factory() as session:
            processor = InboundProcessor(
                session,
                WhatsAppRepository(session),
                build_whatsapp_client(),
                build_speech_provider(),
            )
            refs = await processor.process(payload)
        queued = replied = 0
        for conversation_id in dict.fromkeys(r.conversation_id for r in refs):
            placed = await enqueue_ai_reply(ctx, conversation_id)
            if placed == "queued":
                queued += 1
            elif placed == "no_queue":
                result = await build_reply_coordinator(None).run(conversation_id)
                if result.status != "skipped":
                    replied += 1
        run.note(
            f"сообщений: {len(refs)}, ответов поставлено: {queued}, на месте: {replied}"
        )
        return len(refs)


async def close_stale_conversations(ctx: dict[str, Any]) -> int:
    """Закрыть диалоги, молчащие дольше суток, и поставить их резюме (§3.3).

    Резюме — по заданию на диалог (Ф8): пачка резюме в одном cron не
    укладывалась в таймаут, и закрытые диалоги оставались без резюме навсегда.
    Без очереди (прямой вызов) — на месте, своей сессией и коммитом на каждое.
    """
    async with JobRunRecorder("close_stale_conversations", attempt(ctx)) as run:
        async with session_factory() as session:
            repository = WhatsAppRepository(session)
            closed = await repository.close_stale(datetime.now(UTC) - WINDOW)
            await session.commit()

        redis = (ctx or {}).get("redis")
        queued = summarized = 0
        for conversation_id in closed:
            if redis is not None and hasattr(redis, "enqueue_job"):
                await redis.enqueue_job(SUMMARIZE_CONVERSATION, str(conversation_id))
                queued += 1
                continue
            async with session_factory() as session:
                service = ConversationSummaryService(session, build_ai_provider())
                if await service.build(conversation_id) is not None:
                    await session.commit()
                    summarized += 1
        run.note(
            f"закрыто: {len(closed)}, резюме поставлено: {queued}, на месте: {summarized}"
        )
        return len(closed)
