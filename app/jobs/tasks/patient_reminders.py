"""Напоминания о долге и повторном осмотре (Ф7) — раз в сутки утром.

04:12 UTC = 10:12 по Бишкеку: пациент получает сообщение в рабочее время,
а не ночью. Долг и повторный осмотр — в одном задании: обе рассылки
безопасны к повтору (одно напоминание на источник).
"""

from __future__ import annotations

from typing import Any

from app.core.db import session_factory
from app.jobs.tasks.context import attempt
from app.modules.ai.application.patient_reminders import PatientReminderService
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.shared.whatsapp_cloud import build_whatsapp_client


async def send_patient_reminders(ctx: dict[str, Any]) -> str:
    """Разослать напоминания о долге и повторном осмотре; вернуть сводку."""
    async with JobRunRecorder("send_patient_reminders", attempt(ctx)) as run:
        async with session_factory() as session:
            service = PatientReminderService(session, build_whatsapp_client())
            debts = await service.send_debts()
            recalls = await service.send_recalls()
        summary = f"о долге: {debts}, о повторном осмотре: {recalls}"
        run.note(summary)
        return summary
