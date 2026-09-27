"""Use-case: снять отметку «напомнили», поставленную по ошибке."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.reminders.infrastructure.repositories.reminder_repository import (
    ReminderRepository,
)


class DeleteReminderMarkUseCase:
    def __init__(self, session: AsyncSession, reminders: ReminderRepository) -> None:
        self._session = session
        self._reminders = reminders

    async def execute(self, clinic_id: uuid.UUID, mark_id: uuid.UUID) -> None:
        """Удалить отметку этой клиники; чужая или несуществующая → 404."""
        mark = await self._reminders.get(clinic_id, mark_id)
        if mark is None:
            raise NotFoundError("Отметка не найдена", code="reminder_mark_not_found")
        await self._reminders.delete(mark)
        await self._session.commit()
