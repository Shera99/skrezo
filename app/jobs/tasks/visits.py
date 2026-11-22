"""Напоминания о визите (Ф7) и пуш «приём не подтверждён».

``send_visit_reminders`` — раз в час: приёмы, вошедшие в горизонт, получают
шаблон с кнопками; напоминания, оставшиеся без ответа к контрольному сроку,
закрываются. Обе фазы в одном задании: вторая без первой бессмысленна.

``notify_unconfirmed_visits`` — раз в пять минут: приём, который за час до
начала всё ещё ``scheduled``, уходит событием врачу и администраторам. От
напоминаний не зависит — работает и у клиники без WhatsApp.
"""

from __future__ import annotations

from typing import Any

from app.core.db import session_factory
from app.jobs.tasks.context import attempt
from app.modules.ai.application.visits import VisitReminderService
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.modules.push.application.unconfirmed_service import UnconfirmedVisitService
from app.shared.whatsapp_cloud import build_whatsapp_client


async def send_visit_reminders(ctx: dict[str, Any]) -> str:
    """Отправить напоминания и закрыть оставшиеся без ответа; вернуть сводку."""
    async with JobRunRecorder("send_visit_reminders", attempt(ctx)) as run:
        async with session_factory() as session:
            service = VisitReminderService(session, build_whatsapp_client())
            sent = await service.send_due()
            unanswered = await service.close_unanswered()
        summary = f"напоминаний: {sent}, без ответа: {unanswered}"
        run.note(summary)
        return summary


async def notify_unconfirmed_visits(ctx: dict[str, Any]) -> int:
    """Поднять приёмы, не подтверждённые за час до начала; вернуть число событий."""
    async with JobRunRecorder("notify_unconfirmed_visits", attempt(ctx)) as run:
        async with session_factory() as session:
            emitted = await UnconfirmedVisitService(session).run()
        run.note(f"не подтверждено приёмов: {emitted}")
        return emitted
