"""Задачи WhatsApp-канала: разбор вебхука и закрытие остывших диалогов.

``process_wa_inbound`` ставится вебхуком (тот обязан ответить Meta за 5 секунд
и потому не разбирает payload сам). Повторяется при падении: обработка
идемпотентна — сообщения дедуплицируются по ``wa_message_id``.

``close_stale_conversations`` — cron: диалог без сообщений сутки переходит в
``closed``. Резюме закрытого диалога появится в Ф2 (ассистент) — здесь только
смена статуса.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.db import session_factory
from app.core.jobs import PROCESS_WA_INBOUND
from app.jobs.tasks.context import attempt
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.modules.whatsapp.application.services.inbound import InboundProcessor
from app.modules.whatsapp.domain.window import WINDOW
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.whatsapp_cloud import build_whatsapp_client


async def process_wa_inbound(ctx: dict[str, Any], payload: dict[str, Any]) -> int:
    """Разобрать payload вебхука; вернуть число сохранённых сообщений."""
    async with JobRunRecorder(PROCESS_WA_INBOUND, attempt(ctx)) as run:
        async with session_factory() as session:
            processor = InboundProcessor(
                session, WhatsAppRepository(session), build_whatsapp_client()
            )
            saved = await processor.process(payload)
        run.note(f"сообщений сохранено: {saved}")
        return saved


async def close_stale_conversations(ctx: dict[str, Any]) -> int:
    """Закрыть диалоги, молчащие дольше суток; вернуть число закрытых."""
    async with JobRunRecorder("close_stale_conversations", attempt(ctx)) as run:
        async with session_factory() as session:
            repository = WhatsAppRepository(session)
            closed = await repository.close_stale(datetime.now(UTC) - WINDOW)
            await session.commit()
        run.note(f"закрыто диалогов: {closed}")
        return closed
