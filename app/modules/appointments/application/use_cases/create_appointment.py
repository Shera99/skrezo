"""Use-case: создать приём с манипуляциями по зубам."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.modules.appointments.application.services.booking import booking_guard
from app.modules.appointments.domain.models import ToothInput
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.patients.domain.teeth import is_valid_tooth
from app.modules.schedule.application.services.availability_service import (
    AvailabilityService,
    span_of,
)
from app.shared.db.enums import AppointmentStatus, AppointmentType
from app.shared.db.models.appointment import Appointment


@dataclass(slots=True)
class CreateAppointmentInput:
    patient_id: uuid.UUID
    doctor_membership_id: uuid.UUID | None
    service_id: uuid.UUID | None
    appointment_type: AppointmentType
    scheduled_at: datetime | None
    duration_min: int | None
    comment: str | None
    teeth: list[ToothInput]
    status: AppointmentStatus = AppointmentStatus.SCHEDULED
    # Записать вне рабочих часов врача (право ``appointments.force_booking``).
    force: bool = False


class CreateAppointmentUseCase:
    def __init__(
        self,
        session: AsyncSession,
        appointments: AppointmentRepository,
        availability: AvailabilityService,
    ) -> None:
        self._session = session
        self._appointments = appointments
        self._availability = availability

    async def execute(
        self, clinic_id: uuid.UUID, data: CreateAppointmentInput, *, can_force: bool = False
    ) -> Appointment:
        if data.force and not can_force:
            raise ForbiddenError(
                "Нужно право на запись вне графика врача", code="force_booking_forbidden"
            )
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
        if doctor_id is not None and not await self._availability.is_doctor(clinic_id, doctor_id):
            raise ValidationAppError("Врач не из этой клиники", code="invalid_doctor")

        if data.service_id is not None and not await self._appointments.service_in_clinic(
            clinic_id, data.service_id
        ):
            raise ValidationAppError("Услуга не из этой клиники", code="invalid_service")

        duration_min = await self._availability.resolve_duration(
            clinic_id, data.service_id, data.duration_min
        )
        # Черновик — пациент заведён, но дата ещё не выбрана: слот не занимается,
        # проверять нечего. У обычной записи пустая дата означает «сейчас».
        is_draft = data.status is AppointmentStatus.DRAFT
        scheduled_at = None if is_draft else (data.scheduled_at or datetime.now(UTC))
        # Занятость проверяем только у назначенного врача: без врача слот не занимается.
        if doctor_id is not None and scheduled_at is not None:
            await self._availability.ensure_bookable(
                clinic_id, doctor_id, span_of(scheduled_at, duration_min), force=data.force
            )

        service_ids = [t.service_id for t in data.teeth if t.service_id is not None]
        prices = await self._appointments.service_prices(service_ids)

        # Гвард накрывает и create() (внутри флашит INSERT — там и срабатывает
        # констрейнт), и commit(): ловить только коммит недостаточно.
        async with booking_guard(self._session):
            appointment = await self._appointments.create(
                clinic_id,
                data.patient_id,
                doctor_membership_id=data.doctor_membership_id,
                service_id=data.service_id,
                appointment_type=data.appointment_type,
                scheduled_at=scheduled_at,
                duration_min=duration_min,
                comment=data.comment,
                teeth=data.teeth,
                service_prices=prices,
                status=data.status,
            )
            await self._session.commit()
        return appointment
