"""Зависимости модуля пушей: репозитории и use-case'ы."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.push.application.use_cases.register_device import RegisterDeviceUseCase
from app.modules.push.application.use_cases.unregister_device import UnregisterDeviceUseCase
from app.modules.push.application.use_cases.update_prefs import UpdatePrefsUseCase
from app.modules.push.infrastructure.repositories.device_repository import DeviceRepository
from app.modules.push.infrastructure.repositories.notification_pref_repository import (
    NotificationPrefRepository,
)


def get_device_repository(session: AsyncSession = Depends(get_session)) -> DeviceRepository:
    return DeviceRepository(session)


def get_notification_pref_repository(
    session: AsyncSession = Depends(get_session),
) -> NotificationPrefRepository:
    return NotificationPrefRepository(session)


def get_register_device_use_case(
    session: AsyncSession = Depends(get_session),
    devices: DeviceRepository = Depends(get_device_repository),
) -> RegisterDeviceUseCase:
    return RegisterDeviceUseCase(session, devices)


def get_unregister_device_use_case(
    session: AsyncSession = Depends(get_session),
    devices: DeviceRepository = Depends(get_device_repository),
) -> UnregisterDeviceUseCase:
    return UnregisterDeviceUseCase(session, devices)


def get_update_prefs_use_case(
    session: AsyncSession = Depends(get_session),
    prefs: NotificationPrefRepository = Depends(get_notification_pref_repository),
) -> UpdatePrefsUseCase:
    return UpdatePrefsUseCase(session, prefs)
