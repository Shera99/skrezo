"""Use-case: обновить приём (статус/комментарий/врач/дата)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.appointments.domain.models import AppointmentDetail
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)


class UpdateAppointmentUseCase:
    def __init__(self, session: AsyncSession, appointments: AppointmentRepository) -> None:
        self._session = session
        self._appointments = appointments

    async def execute(
        self, clinic_id: uuid.UUID, appointment_id: uuid.UUID, fields: dict[str, Any]
    ) -> AppointmentDetail:
        appointment = await self._appointments.get(clinic_id, appointment_id)
        if appointment is None:
            raise NotFoundError("Приём не найден", code="appointment_not_found")
        await self._appointments.update(appointment, fields)
        await self._session.commit()

        patient_name = await self._appointments.patient_name(appointment.patient_id)
        doctor_name = await self._appointments.doctor_name(appointment.doctor_membership_id)
        teeth = list(await self._appointments.teeth(appointment_id))
        return AppointmentDetail(
            appointment=appointment,
            patient_name=patient_name,
            doctor_name=doctor_name,
            teeth=teeth,
        )
