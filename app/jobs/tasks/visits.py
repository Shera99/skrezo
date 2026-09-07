"""Напоминания о визите (Ф7): рассылка за сутки и контроль ответа.

Раз в час: приёмы, вошедшие в горизонт, получают шаблон с кнопками; те, что
остались без ответа к контрольному сроку, уходят событием администратору.
Обе фазы в одном задании: вторая без первой бессмысленна.
"""

from __future__ import annotations

from typing import Any

from app.core.db import session_factory
from app.jobs.tasks.context import attempt
from app.modules.ai.application.visits import VisitReminderService
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.shared.whatsapp_cloud import build_whatsapp_client


async def send_visit_reminders(ctx: dict[str, Any]) -> str:
    """Отправить напоминания и поднять неподтверждённые; вернуть сводку."""
    async with JobRunRecorder("send_visit_reminders", attempt(ctx)) as run:
        async with session_factory() as session:
            service = VisitReminderService(session, build_whatsapp_client())
            sent = await service.send_due()
            escalated = await service.escalate_unanswered()
        summary = f"напоминаний: {sent}, без ответа: {escalated}"
        run.note(summary)
        return summary
