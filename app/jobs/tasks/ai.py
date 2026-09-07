"""Задания контура ассистента (Ф8): ответ на диалог и резюме — по одному на задание.

Раньше ответ жил внутри разбора вебхука и шёл по диалогам последовательно:
Meta пакует сообщения разных людей в один запрос, и одно задание растягивалось
на N × (3–30 с) при таймауте 120 с. arq убивал его на середине, повтор находил
все сообщения уже записанными (дедуп) — и диалоги, не успевшие получить
ответ, молча оставались без него. Теперь приём только сохраняет и ставит по
заданию на диалог; у ответа свой таймаут, свои повторы и свой лок.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.db import session_factory
from app.core.jobs import AI_REPLY, RUN_AI_REGRESSION, SUMMARIZE_CONVERSATION
from app.jobs.tasks.context import attempt
from app.modules.ai.application.regression_runner import RegressionRunner
from app.modules.ai.application.reply_coordinator import (
    ConversationLock,
    CoordinatorResult,
    ReplyCoordinator,
)
from app.modules.ai.application.summary import ConversationSummaryService
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.shared.ai.factory import build_ai_provider
from app.shared.db.enums import AiMode
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.clinic import Clinic
from app.shared.whatsapp_cloud import build_whatsapp_client


def _queue(ctx: dict[str, Any] | None) -> Any:
    """Redis arq из контекста воркера; ``None`` — прямой вызов без очереди."""
    redis = (ctx or {}).get("redis")
    return redis if redis is not None and hasattr(redis, "enqueue_job") else None


async def assistant_enabled(conversation_id: uuid.UUID) -> bool:
    """Включён ли ассистент у клиники диалога.

    Проверяется до постановки задания: клиникам с ``ai_mode=off`` (а это
    большинство на старте) незачем плодить по пустому заданию на каждое
    сообщение — при десятках тысяч входящих в день это ощутимо.
    """
    async with session_factory() as session:
        mode = (
            await session.execute(
                select(Clinic.ai_mode)
                .join(AiConversation, AiConversation.clinic_id == Clinic.id)
                .where(AiConversation.id == conversation_id)
            )
        ).scalar_one_or_none()
    return mode is not None and mode is not AiMode.OFF


async def enqueue_ai_reply(ctx: dict[str, Any] | None, conversation_id: uuid.UUID) -> str:
    """Поставить ответ с паузой «дописал ли пациент?».

    Возвращает ``queued`` (задание в очереди), ``off`` (ассистент выключен —
    делать нечего) или ``no_queue`` (прямой вызов без Redis — отвечать на месте).
    """
    if not await assistant_enabled(conversation_id):
        return "off"
    redis = _queue(ctx)
    if redis is None:
        return "no_queue"
    await redis.enqueue_job(
        AI_REPLY, str(conversation_id), 0, _defer_by=settings.ai_reply_debounce_seconds
    )
    return "queued"


def build_reply_coordinator(ctx: dict[str, Any] | None) -> ReplyCoordinator:
    """Координатор под контекст воркера; без очереди — без лока и отложек."""
    redis = _queue(ctx)
    lock: ConversationLock | None = None
    enqueue = None
    if redis is not None:
        lock = ConversationLock(redis, ttl_seconds=settings.ai_reply_job_timeout)

        async def enqueue(conversation_id: uuid.UUID, defer_count: int) -> None:
            await redis.enqueue_job(
                AI_REPLY,
                str(conversation_id),
                defer_count,
                _defer_by=settings.ai_reply_defer_seconds,
            )

    return ReplyCoordinator(
        session_factory=session_factory,
        provider_factory=lambda: build_ai_provider(redis),
        wa_client_factory=build_whatsapp_client,
        lock=lock,
        enqueue=enqueue,
        max_defers=settings.ai_reply_max_defers,
    )


async def ai_reply(ctx: dict[str, Any], conversation_id: str, defer_count: int = 0) -> str:
    """Ответ ассистента на диалог; вернуть, чем кончилось."""
    async with JobRunRecorder(AI_REPLY, attempt(ctx)) as run:
        result: CoordinatorResult = await build_reply_coordinator(ctx).run(
            uuid.UUID(conversation_id), defer_count
        )
        run.note(f"диалог {conversation_id}: {result.status}")
        return result.status


async def summarize_conversation(ctx: dict[str, Any], conversation_id: str) -> bool:
    """Резюме одного закрытого диалога (§3.3); ``True`` — резюме появилось."""
    async with JobRunRecorder(SUMMARIZE_CONVERSATION, attempt(ctx)) as run:
        async with session_factory() as session:
            service = ConversationSummaryService(session, build_ai_provider(_queue(ctx)))
            summary = await service.build(uuid.UUID(conversation_id))
            if summary is not None:
                await session.commit()
        run.note(f"диалог {conversation_id}: {'резюме готово' if summary else 'пропущен'}")
        return summary is not None


async def run_ai_regression(ctx: dict[str, Any], run_id: str) -> str:
    """Прогон регрессии клиники (Ф6); повтор задания продолжает с неоценённых кейсов."""
    async with JobRunRecorder(RUN_AI_REGRESSION, attempt(ctx)) as run:
        async with session_factory() as session:
            runner = RegressionRunner(
                session, build_ai_provider(_queue(ctx)), build_whatsapp_client()
            )
            summary = await runner.run(uuid.UUID(run_id))
        run.note(summary)
        return summary
