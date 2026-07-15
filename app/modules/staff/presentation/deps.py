"""Зависимости управления сотрудниками."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.staff.application.services.staff_notification_service import (
    StaffNotificationService,
)
from app.modules.staff.application.use_cases.add_staff import AddStaffUseCase
from app.modules.staff.application.use_cases.list_staff import ListStaffUseCase
from app.modules.staff.application.use_cases.remove_staff import RemoveStaffUseCase
from app.modules.staff.application.use_cases.reset_staff_password import ResetStaffPasswordUseCase
from app.modules.staff.application.use_cases.update_staff import UpdateStaffUseCase
from app.modules.staff.infrastructure.repositories.staff_repository import StaffRepository


def get_staff_repository(session: AsyncSession = Depends(get_session)) -> StaffRepository:
    return StaffRepository(session)


def get_staff_notification_service() -> StaffNotificationService:
    return StaffNotificationService()


def get_list_staff_use_case(
    staff: StaffRepository = Depends(get_staff_repository),
) -> ListStaffUseCase:
    return ListStaffUseCase(staff)


def get_add_staff_use_case(
    session: AsyncSession = Depends(get_session),
    staff: StaffRepository = Depends(get_staff_repository),
    notifier: StaffNotificationService = Depends(get_staff_notification_service),
) -> AddStaffUseCase:
    return AddStaffUseCase(session, staff, notifier)


def get_update_staff_use_case(
    session: AsyncSession = Depends(get_session),
    staff: StaffRepository = Depends(get_staff_repository),
) -> UpdateStaffUseCase:
    return UpdateStaffUseCase(session, staff)


def get_remove_staff_use_case(
    session: AsyncSession = Depends(get_session),
    staff: StaffRepository = Depends(get_staff_repository),
) -> RemoveStaffUseCase:
    return RemoveStaffUseCase(session, staff)


def get_reset_staff_password_use_case(
    session: AsyncSession = Depends(get_session),
    staff: StaffRepository = Depends(get_staff_repository),
) -> ResetStaffPasswordUseCase:
    return ResetStaffPasswordUseCase(session, staff)
