"""Use-case: удалить отсутствие врача по id."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


class DeleteTimeOffUseCase:
    def __init__(self, session: AsyncSession, schedule: ScheduleRepository) -> None:
        self._session = session
        self._schedule = schedule

    async def execute(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID, time_off_id: uuid.UUID
    ) -> None:
        """Удалить отсутствие в рамках (клиника, врач).

        Скоуп по клинике и врачу закрывает IDOR: чужую запись не удалить — её
        просто не окажется под условием, и это неотличимо от «нет такой» → 404.
        """
        deleted = await self._schedule.delete_time_off(clinic_id, membership_id, time_off_id)
        if not deleted:
            raise NotFoundError("Отсутствие не найдено", code="time_off_not_found")
        await self._session.commit()
