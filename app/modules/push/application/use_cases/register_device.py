"""Use-case: зарегистрировать устройство на пуш-уведомления."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.push.infrastructure.repositories.device_repository import DeviceRepository
from app.shared.db.enums import DevicePlatform, Lang
from app.shared.db.models.device import Device


class RegisterDeviceUseCase:
    def __init__(self, session: AsyncSession, devices: DeviceRepository) -> None:
        self._session = session
        self._devices = devices

    async def execute(
        self,
        *,
        account_id: uuid.UUID,
        clinic_id: uuid.UUID,
        token: str,
        platform: DevicePlatform,
        app_version: str | None,
        locale: Lang,
    ) -> Device:
        device = await self._devices.upsert(
            token=token,
            account_id=account_id,
            clinic_id=clinic_id,
            platform=platform,
            app_version=app_version,
            locale=locale,
        )
        await self._session.commit()
        return device
