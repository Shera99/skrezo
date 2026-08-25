"""Use-case: снять устройство с пуш-уведомлений (выход из приложения)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.push.infrastructure.repositories.device_repository import DeviceRepository


class UnregisterDeviceUseCase:
    def __init__(self, session: AsyncSession, devices: DeviceRepository) -> None:
        self._session = session
        self._devices = devices

    async def execute(self, token: str, *, account_id: uuid.UUID) -> None:
        await self._devices.delete_by_token(token, account_id=account_id)
        await self._session.commit()
