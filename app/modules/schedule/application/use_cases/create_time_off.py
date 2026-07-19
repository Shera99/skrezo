"""Use-case: завести отсутствие врача (отпуск, больничный, разовая недоступность)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.schedule.domain.models import TimeOffRecord
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


class CreateTimeOffUseCase:
    def __init__(self, session: AsyncSession, schedule: ScheduleRepository) -> None:
        self._session = session
        self._schedule = schedule

    async def execute(
        self,
        clinic_id: uuid.UUID,
        membership_id: uuid.UUID,
        starts_at: datetime,
        ends_at: datetime,
        reason: str | None,
    ) -> TimeOffRecord:
        """Создать отсутствие для врача этой клиники.

        Врач обязан быть практикующим членством этой клиники (иначе правка чужого
        расписания). Интервал абсолютный и перекрывает как график врача, так и часы
        клиники: пересечение делает слот недоступным. Уже созданные приёмы **не**
        пересчитываются — как и при смене графика (это осознанный выбор v1).
        """
        if not await self._schedule.is_doctor_in_clinic(clinic_id, membership_id):
            raise NotFoundError("Врач не найден в этой клинике", code="doctor_not_found")
        if ends_at <= starts_at:
            raise ValidationAppError(
                "Конец отсутствия должен быть позже начала", code="invalid_time_off"
            )
        record = await self._schedule.create_time_off(
            clinic_id, membership_id, starts_at, ends_at, reason
        )
        await self._session.commit()
        return record
