"""Зависимости модуля приёмов."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.appointments.application.use_cases.apply_discount import ApplyDiscountUseCase
from app.modules.appointments.application.use_cases.complete_appointment import (
    CompleteAppointmentUseCase,
)
from app.modules.appointments.application.use_cases.create_appointment import (
    CreateAppointmentUseCase,
)
from app.modules.appointments.application.use_cases.get_appointment import GetAppointmentUseCase
from app.modules.appointments.application.use_cases.list_appointments import ListAppointmentsUseCase
from app.modules.appointments.application.use_cases.replace_items import ReplaceItemsUseCase
from app.modules.appointments.application.use_cases.update_appointment import (
    UpdateAppointmentUseCase,
)
from app.modules.appointments.infrastructure.repositories.appointment_repository import (
    AppointmentRepository,
)
from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.cash.application.services.cash_service import CashService, build_cash_service
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
    audit: AuditService = Depends(get_audit_service),
) -> UpdateAppointmentUseCase:
    return UpdateAppointmentUseCase(session, appointments, availability, audit)


def get_cash_service(session: AsyncSession = Depends(get_session)) -> CashService:
    """Кассовый сервис из модуля кассы (общие правила проведения оплаты)."""
    return build_cash_service(session)


def get_replace_items_use_case(
    session: AsyncSession = Depends(get_session),
    appointments: AppointmentRepository = Depends(get_appointment_repository),
    cash: CashService = Depends(get_cash_service),
) -> ReplaceItemsUseCase:
    return ReplaceItemsUseCase(session, appointments, cash)


def get_apply_discount_use_case(
    session: AsyncSession = Depends(get_session),
    appointments: AppointmentRepository = Depends(get_appointment_repository),
    cash: CashService = Depends(get_cash_service),
    audit: AuditService = Depends(get_audit_service),
) -> ApplyDiscountUseCase:
    return ApplyDiscountUseCase(session, appointments, cash, audit)


def get_complete_appointment_use_case(
    session: AsyncSession = Depends(get_session),
    appointments: AppointmentRepository = Depends(get_appointment_repository),
    cash: CashService = Depends(get_cash_service),
) -> CompleteAppointmentUseCase:
    return CompleteAppointmentUseCase(session, appointments, cash)
