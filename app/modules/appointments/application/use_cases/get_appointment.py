"""Use-case: детали приёма (зубы, услуги, врач)."""

from __future__ import annotations

import uuid

from app.core.errors import NotFoundError
from app.modules.appointments.domain.models import AppointmentDetail
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)


class GetAppointmentUseCase:
    def __init__(self, appointments: AppointmentRepository) -> None:
        self._appointments = appointments

    async def execute(self, clinic_id: uuid.UUID, appointment_id: uuid.UUID) -> AppointmentDetail:
        appointment = await self._appointments.get(clinic_id, appointment_id)
        if appointment is None:
            raise NotFoundError("Приём не найден", code="appointment_not_found")
        patient_name = await self._appointments.patient_name(appointment.patient_id)
        doctor_name = await self._appointments.doctor_name(appointment.doctor_membership_id)
        teeth = list(await self._appointments.teeth(appointment_id))
        return AppointmentDetail(
            appointment=appointment,
            patient_name=patient_name,
            doctor_name=doctor_name,
            teeth=teeth,
        )
