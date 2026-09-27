"""Зависимости модуля напоминаний."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.reminders.application.use_cases.delete_reminder_mark import (
    DeleteReminderMarkUseCase,
)
from app.modules.reminders.application.use_cases.list_reminder_marks import (
    ListReminderMarksUseCase,
)
from app.modules.reminders.application.use_cases.mark_reminded import MarkRemindedUseCase
from app.modules.reminders.infrastructure.repositories.reminder_repository import (
    ReminderRepository,
)


def get_reminder_repository(session: AsyncSession = Depends(get_session)) -> ReminderRepository:
    return ReminderRepository(session)


def get_mark_reminded_use_case(
    session: AsyncSession = Depends(get_session),
    reminders: ReminderRepository = Depends(get_reminder_repository),
) -> MarkRemindedUseCase:
    return MarkRemindedUseCase(session, reminders)


def get_list_reminder_marks_use_case(
    reminders: ReminderRepository = Depends(get_reminder_repository),
) -> ListReminderMarksUseCase:
    return ListReminderMarksUseCase(reminders)


def get_delete_reminder_mark_use_case(
    session: AsyncSession = Depends(get_session),
    reminders: ReminderRepository = Depends(get_reminder_repository),
) -> DeleteReminderMarkUseCase:
    return DeleteReminderMarkUseCase(session, reminders)
