"""Use-case: журнал отметок «напомнили» с фильтрами."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date

from app.modules.reminders.domain.models import ReminderKind, ReminderMarksPage
from app.modules.reminders.infrastructure.repositories.reminder_repository import (
    ReminderRepository,
)
from app.modules.reports.domain.period import ReportPeriod, resolve_period, resolve_tz


class ListReminderMarksUseCase:
    def __init__(self, reminders: ReminderRepository) -> None:
        self._reminders = reminders

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        patient_id: uuid.UUID | None,
        kind: ReminderKind | None,
        date_from: date | None,
        date_to: date | None,
        page: int,
        page_size: int,
    ) -> ReminderMarksPage:
        """Отметки клиники за период (последние сверху).

        Даты без времени — сутки клиники целиком, как в кассе и отчётах; без
        дат — за всё время. Момент отметки отдаётся в таймзоне клиники.
        """
        tz = resolve_tz(await self._reminders.clinic_timezone(clinic_id))
        unbounded = date_from is None and date_to is None
        bounds = resolve_period(ReportPeriod.ALL if unbounded else None, date_from, date_to, tz)
        records, total = await self._reminders.list_page(
            clinic_id,
            patient_id=patient_id,
            kind=kind,
            date_from=bounds.start,
            date_to=bounds.end,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        items = [replace(r, marked_at=r.marked_at.astimezone(tz)) for r in records]
        return ReminderMarksPage(
            items=items, total_count=total, current_page=page, page_size=page_size
        )
