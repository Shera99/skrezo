"""Зависимости модуля приёмов."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.appointments.application.use_cases.create_appointment import (
    CreateAppointmentUseCase,
)
from app.modules.appointments.application.use_cases.get_appointment import GetAppointmentUseCase
from app.modules.appointments.application.use_cases.list_appointments import ListAppointmentsUseCase
from app.modules.appointments.application.use_cases.update_appointment import (
    UpdateAppointmentUseCase,
)
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.schedule.application.services.availability_service import (
    AvailabilityService,
    build_availability_service,
)


def get_appointment_repository(
    session: AsyncSession = Depends(get_session),
) -> AppointmentRepository:
    return AppointmentRepository(session)


def get_availability_service(
    session: AsyncSession = Depends(get_session),
) -> AvailabilityService:
    """Сервис доступности врача из модуля расписания (общее определение врача)."""
    return build_availability_service(session)


def get_create_appointment_use_case(
    session: AsyncSession = Depends(get_session),
    appointments: AppointmentRepository = Depends(get_appointment_repository),
    availability: AvailabilityService = Depends(get_availability_service),
) -> CreateAppointmentUseCase:
    return CreateAppointmentUseCase(session, appointments, availability)


def get_list_appointments_use_case(
    appointments: AppointmentRepository = Depends(get_appointment_repository),
) -> ListAppointmentsUseCase:
    return ListAppointmentsUseCase(appointments)


def get_get_appointment_use_case(
    appointments: AppointmentRepository = Depends(get_appointment_repository),
) -> GetAppointmentUseCase:
    return GetAppointmentUseCase(appointments)


def get_update_appointment_use_case(
    session: AsyncSession = Depends(get_session),
    appointments: AppointmentRepository = Depends(get_appointment_repository),
    availability: AvailabilityService = Depends(get_availability_service),
) -> UpdateAppointmentUseCase:
    return UpdateAppointmentUseCase(session, appointments, availability)
