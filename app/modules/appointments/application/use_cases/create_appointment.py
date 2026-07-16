"""Use-case: создать приём с манипуляциями по зубам."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.modules.appointments.domain.models import ToothInput
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.patients.domain.teeth import is_valid_tooth
from app.shared.db.enums import AppointmentType
from app.shared.db.models.appointment import Appointment


@dataclass(slots=True)
class CreateAppointmentInput:
    patient_id: uuid.UUID
    doctor_membership_id: uuid.UUID | None
    appointment_type: AppointmentType
    scheduled_at: datetime | None
    comment: str | None
    teeth: list[ToothInput]


class CreateAppointmentUseCase:
    def __init__(self, session: AsyncSession, appointments: AppointmentRepository) -> None:
        self._session = session
        self._appointments = appointments

    async def execute(self, clinic_id: uuid.UUID, data: CreateAppointmentInput) -> Appointment:
        if not await self._appointments.patient_exists(data.patient_id):
            raise NotFoundError("Пациент не найден", code="patient_not_found")

        for item in data.teeth:
            if not is_valid_tooth(item.tooth):
                raise ValidationAppError(
                    f"Некорректный номер зуба (FDI): {item.tooth}", code="invalid_tooth"
                )
            if item.service_id is not None and not await self._appointments.service_in_clinic(
                clinic_id, item.service_id
            ):
                raise ValidationAppError("Услуга не из этой клиники", code="invalid_service")

        doctor_id = data.doctor_membership_id
        if doctor_id is not None:
            ok = await self._appointments.membership_in_clinic(clinic_id, doctor_id)
            if not ok:
                raise ValidationAppError("Врач не из этой клиники", code="invalid_doctor")

        service_ids = [t.service_id for t in data.teeth if t.service_id is not None]
        prices = await self._appointments.service_prices(service_ids)

        appointment = await self._appointments.create(
            clinic_id,
            data.patient_id,
            doctor_membership_id=data.doctor_membership_id,
            appointment_type=data.appointment_type,
            scheduled_at=data.scheduled_at,
            comment=data.comment,
            teeth=data.teeth,
            service_prices=prices,
        )
        await self._session.commit()
        return appointment
