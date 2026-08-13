"""Use-case: снять индивидуальный график врача — вернуть его на часы клиники.

``PUT`` умеет только перезаписать все 7 дней, и «как у клиники» через него не
выражается: пришлось бы сохранить копию текущих часов клиники, а копия не поедет
за их изменением. Поэтому возврат к часам клиники — это удаление строк:
их отсутствие и есть «работает как клиника».
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


class ClearDoctorHoursUseCase:
    def __init__(self, session: AsyncSession, schedule: ScheduleRepository) -> None:
        self._session = session
        self._schedule = schedule

    async def execute(self, clinic_id: uuid.UUID, membership_id: uuid.UUID) -> bool:
        """Снять график; вернуть, был ли он вообще.

        Врача не из этой клиники — 404, как и у ``PUT``: иначе чужой график можно
        было бы сбрасывать вслепую. Повторный вызов на враче без графика —
        не ошибка: результат тот же, что и просили.
        """
        if not await self._schedule.is_doctor_in_clinic(clinic_id, membership_id):
            raise NotFoundError("Врач не найден в этой клинике", code="doctor_not_found")
        removed = await self._schedule.clear_doctor_hours(clinic_id, membership_id)
        await self._session.commit()
        return removed > 0
