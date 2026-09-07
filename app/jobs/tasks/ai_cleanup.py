"""Ретеншн истории ассистента (Ф8): журнал инструментов и старая переписка.

При 100–150 клиниках ``ai_message`` и ``ai_tool_call`` растут до десятков
миллионов строк в год. Журнал вызовов инструментов — технический след, ему
хватает трёх месяцев. Переписка закрытых диалогов хранится дольше (срок в
настройках), после чего удаляется вместе с медиа на диске; резюме диалогов
(``ai_summary``) остаются — это долгая память бота о пациенте. Открытые
диалоги не трогаются никогда.

Удаление порциями: одна огромная транзакция держала бы блокировки и WAL
дольше, чем нужно ночной задаче.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import structlog
from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import session_factory
from app.jobs.tasks.context import attempt
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.shared.db.enums import AiConversationStatus
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_tool_call import AiToolCall
from app.shared.storage.files import storage_root

logger = structlog.get_logger(__name__)

#: Строк за одну транзакцию удаления.
BATCH = 5_000


async def cleanup_ai_history(ctx: dict[str, Any]) -> str:
    """Удалить отжившее; вернуть сводку."""
    async with JobRunRecorder("cleanup_ai_history", attempt(ctx)) as run:
        now = datetime.now(UTC)
        async with session_factory() as session:
            tool_calls = await _purge_tool_calls(
                session, now - timedelta(days=settings.ai_tool_call_retention_days)
            )
            messages, files = await _purge_messages(
                session, now - timedelta(days=settings.ai_message_retention_days)
            )
            conversations = await _purge_empty_conversations(
                session, now - timedelta(days=settings.ai_message_retention_days)
            )
        summary = (
            f"вызовов инструментов: {tool_calls}, сообщений: {messages} "
            f"(файлов: {files}), пустых диалогов: {conversations}"
        )
        run.note(summary)
        return summary


async def _purge_tool_calls(session: AsyncSession, before: datetime) -> int:
    total = 0
    while True:
        ids = select(AiToolCall.id).where(AiToolCall.created_at < before).limit(BATCH)
        result = await session.execute(delete(AiToolCall).where(AiToolCall.id.in_(ids)))
        await session.commit()
        count = int(getattr(result, "rowcount", 0) or 0)
        total += count
        if count < BATCH:
            return total


async def _purge_messages(session: AsyncSession, before: datetime) -> tuple[int, int]:
    """Сообщения закрытых диалогов старше срока — порциями, вместе с медиа."""
    closed = exists().where(
        AiConversation.id == AiMessage.conversation_id,
        AiConversation.status == AiConversationStatus.CLOSED,
    )
    total, files = 0, 0
    while True:
        rows = (
            await session.execute(
                select(AiMessage.id, AiMessage.media_path)
                .where(AiMessage.created_at < before, closed)
                .order_by(AiMessage.id)
                .limit(BATCH)
            )
        ).all()
        if not rows:
            return total, files
        await session.execute(delete(AiMessage).where(AiMessage.id.in_([r[0] for r in rows])))
        await session.commit()
        total += len(rows)
        for _, media_path in rows:
            if media_path and await _remove_file(media_path):
                files += 1
        if len(rows) < BATCH:
            return total, files


async def _purge_empty_conversations(session: AsyncSession, before: datetime) -> int:
    """Закрытые диалоги, у которых не осталось сообщений и всё было давно."""
    has_messages = exists().where(AiMessage.conversation_id == AiConversation.id)
    result = await session.execute(
        delete(AiConversation).where(
            AiConversation.status == AiConversationStatus.CLOSED,
            AiConversation.last_msg_at < before,
            ~has_messages,
        )
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def _remove_file(relative: str) -> bool:
    """Удалить файл из хранилища; отсутствие файла — не ошибка."""
    target = storage_root() / Path(relative)
    try:
        await anyio.to_thread.run_sync(target.unlink)
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("ai_cleanup_file_failed", path=relative, error=str(exc))
        return False
    return True
