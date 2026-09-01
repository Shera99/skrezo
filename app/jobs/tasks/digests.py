"""Задача сводок дня: раз в 5 минут проверить, у кого наступил целевой момент.

Тонкий адаптер над :class:`DigestService` — вся логика там, чтобы её можно
было вызвать из теста с фиксированным «сейчас», не поднимая воркер. Повторный
тик безвреден: одна сводка на клинику в день (дедуп в ``push_event``).
"""

from __future__ import annotations

from typing import Any

from app.core.db import session_factory
from app.jobs.tasks.context import attempt
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.modules.push.application.digest_service import DigestService


async def send_daily_digests(ctx: dict[str, Any]) -> int:
    """Эмитить созревшие сводки; вернуть число событий."""
    async with JobRunRecorder("send_daily_digests", attempt(ctx)) as run:
        async with session_factory() as session:
            emitted = await DigestService(session).run()
        run.note(f"сводок эмитировано: {emitted}")
        return emitted
