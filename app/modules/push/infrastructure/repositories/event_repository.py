"""Репозиторий outbox: очередь необработанных событий для диспетчера."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.event import Event

#: После стольких неудач событие больше не берётся (остаётся алертом в журнале).
MAX_ATTEMPTS = 5


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def pending_batch(self, limit: int = 50) -> Sequence[Event]:
        """Пачка необработанных событий в порядке появления.

        ``FOR UPDATE SKIP LOCKED`` — два воркера не возьмут одно событие и не
        встанут друг за другом; ``attempts < MAX_ATTEMPTS`` оставляет
        безнадёжные строки лежать журналом, а не крутиться вечно.
        """
        rows = (
            await self._session.execute(
                select(Event)
                .where(Event.processed_at.is_(None), Event.attempts < MAX_ATTEMPTS)
                .order_by(Event.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
        return list(rows)

    def mark_processed(self, event: Event) -> None:
        event.processed_at = datetime.now(UTC)

    async def delete_older_than(self, cutoff: datetime) -> int:
        """Уборка обработанных событий по возрасту (хранение — 12 месяцев, §14).

        Необработанные (упавшие все 5 попыток) не трогаем: это алерт, а не
        мусор — их след должен пережить уборку и попасться на глаза.
        """
        result = await self._session.execute(
            delete(Event)
            .where(Event.occurred_at < cutoff, Event.processed_at.is_not(None))
            .returning(Event.id)
        )
        return len(list(result.scalars()))

    def mark_failed(self, event: Event, error: str) -> None:
        event.attempts += 1
        event.last_error = error[:1000]
