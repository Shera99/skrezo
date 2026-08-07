"""Use-case: создать приём с манипуляциями по зубам."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.modules.appointments.application.services.booking import booking_guard
from app.modules.appointments.application.services.items import (
    ensure_items_valid,
    resolve_service_prices,
)
from app.modules.appointments.domain.models import ItemInput
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
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
    items: list[ItemInput]
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

        await ensure_items_valid(clinic_id, data.items, self._appointments)

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
        # проверять нечего. Это ЕДИНСТВЕННЫЙ приём без даты.
        #
        # Прежде пустая дата у обычной записи означала «сейчас», и это подводило:
        # опечатка в имени поля (``startAt`` вместо ``scheduledAt``) молча
        # записывала приём на текущий момент, а на повторной попытке приходил
        # 409 «слот занят» — ошибка про чужую подсистему вместо «нет даты».
        # Запись «прямо сейчас» никуда не делась: время просто передаётся явно.
        is_draft = data.status is AppointmentStatus.DRAFT
        if not is_draft and data.scheduled_at is None:
            raise ValidationAppError(
                "Укажите дату приёма: без неё запись возможна только как черновик",
                code="scheduled_at_required",
            )
        scheduled_at = None if is_draft else data.scheduled_at
        # Занятость проверяем только у назначенного врача: без врача слот не занимается.
        if doctor_id is not None and scheduled_at is not None:
            await self._availability.ensure_bookable(
                clinic_id, doctor_id, span_of(scheduled_at, duration_min), force=data.force
            )

        prices = await resolve_service_prices(data.items, self._appointments)

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
                items=data.items,
                service_prices=prices,
                status=data.status,
            )
            await self._session.commit()
        return appointment
