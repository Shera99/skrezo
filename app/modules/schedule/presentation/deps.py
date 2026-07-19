"""Зависимости модуля расписания."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.schedule.application.services.availability_service import AvailabilityService
from app.modules.schedule.application.use_cases.create_time_off import CreateTimeOffUseCase
from app.modules.schedule.application.use_cases.delete_time_off import DeleteTimeOffUseCase
from app.modules.schedule.application.use_cases.get_day import GetDayUseCase
from app.modules.schedule.application.use_cases.get_doctor_hours import GetDoctorHoursUseCase
from app.modules.schedule.application.use_cases.get_slots import GetSlotsUseCase
from app.modules.schedule.application.use_cases.get_working_hours import GetWorkingHoursUseCase
from app.modules.schedule.application.use_cases.list_time_off import ListTimeOffUseCase
from app.modules.schedule.application.use_cases.set_doctor_hours import SetDoctorHoursUseCase
from app.modules.schedule.application.use_cases.set_working_hours import SetWorkingHoursUseCase
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository


def get_schedule_repository(session: AsyncSession = Depends(get_session)) -> ScheduleRepository:
    return ScheduleRepository(session)


def get_availability_service(
    schedule: ScheduleRepository = Depends(get_schedule_repository),
) -> AvailabilityService:
    return AvailabilityService(schedule)


def get_get_day_use_case(
    schedule: ScheduleRepository = Depends(get_schedule_repository),
    availability: AvailabilityService = Depends(get_availability_service),
) -> GetDayUseCase:
    return GetDayUseCase(schedule, availability)


def get_get_slots_use_case(
    schedule: ScheduleRepository = Depends(get_schedule_repository),
    availability: AvailabilityService = Depends(get_availability_service),
) -> GetSlotsUseCase:
    return GetSlotsUseCase(schedule, availability)


def get_get_working_hours_use_case(
    schedule: ScheduleRepository = Depends(get_schedule_repository),
) -> GetWorkingHoursUseCase:
    return GetWorkingHoursUseCase(schedule)


def get_set_working_hours_use_case(
    session: AsyncSession = Depends(get_session),
    schedule: ScheduleRepository = Depends(get_schedule_repository),
) -> SetWorkingHoursUseCase:
    return SetWorkingHoursUseCase(session, schedule)


def get_get_doctor_hours_use_case(
    schedule: ScheduleRepository = Depends(get_schedule_repository),
) -> GetDoctorHoursUseCase:
    return GetDoctorHoursUseCase(schedule)


def get_set_doctor_hours_use_case(
    session: AsyncSession = Depends(get_session),
    schedule: ScheduleRepository = Depends(get_schedule_repository),
) -> SetDoctorHoursUseCase:
    return SetDoctorHoursUseCase(session, schedule)


def get_create_time_off_use_case(
    session: AsyncSession = Depends(get_session),
    schedule: ScheduleRepository = Depends(get_schedule_repository),
) -> CreateTimeOffUseCase:
    return CreateTimeOffUseCase(session, schedule)


def get_list_time_off_use_case(
    schedule: ScheduleRepository = Depends(get_schedule_repository),
    availability: AvailabilityService = Depends(get_availability_service),
) -> ListTimeOffUseCase:
    return ListTimeOffUseCase(schedule, availability)


def get_delete_time_off_use_case(
    session: AsyncSession = Depends(get_session),
    schedule: ScheduleRepository = Depends(get_schedule_repository),
) -> DeleteTimeOffUseCase:
    return DeleteTimeOffUseCase(session, schedule)
