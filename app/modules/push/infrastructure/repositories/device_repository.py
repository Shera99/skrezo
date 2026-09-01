"""Репозиторий устройств: регистрация токена (upsert) и снятие при выходе."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import DevicePlatform, Lang
from app.shared.db.models.device import Device


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_token(self, token: str) -> Device | None:
        return (
            await self._session.execute(select(Device).where(Device.token == token))
        ).scalar_one_or_none()

    async def upsert(
        self,
        *,
        token: str,
        account_id: uuid.UUID,
        clinic_id: uuid.UUID,
        platform: DevicePlatform,
        app_version: str | None,
        locale: Lang,
    ) -> Device:
        """Зарегистрировать устройство: повторная регистрация — не дубль.

        Токен уникален на всю платформу, а не на пару (аккаунт, клиника): один
        и тот же токен с новыми ``account_id``/``clinic_id`` — это переезд
        устройства (сменили клинику, вошёл другой сотрудник), а не отдельная
        запись. ``platform`` при этом не трогаем: она задаётся один раз при
        создании, токен FCM/APNs всегда одной платформы.
        """
        device = await self.get_by_token(token)
        if device is not None:
            device.account_id = account_id
            device.clinic_id = clinic_id
            device.app_version = app_version
            device.locale = locale
            device.last_seen_at = datetime.now(UTC)
            await self._session.flush()
            return device
        device = Device(
            token=token,
            account_id=account_id,
            clinic_id=clinic_id,
            platform=platform,
            app_version=app_version,
            locale=locale,
        )
        self._session.add(device)
        await self._session.flush()
        return device

    async def delete_by_token(self, token: str, *, account_id: uuid.UUID) -> None:
        """Снять устройство — только своё (владение по ``account_id``).

        Клиника здесь не проверяется намеренно: выход из приложения снимает
        устройство целиком, независимо от того, для какой клиники токен был
        зарегистрирован последним. Идемпотентно — отсутствие строки не ошибка.
        """
        await self._session.execute(
            delete(Device).where(Device.token == token, Device.account_id == account_id)
        )
        await self._session.flush()

    async def list_by_clinic(self, clinic_id: uuid.UUID) -> Sequence[Device]:
        """Все устройства клиники — для тихого фан-аута ``sync.day`` на коллектив."""
        result = await self._session.execute(select(Device).where(Device.clinic_id == clinic_id))
        return result.scalars().all()

    async def list_by_accounts(
        self, clinic_id: uuid.UUID, account_ids: Sequence[uuid.UUID]
    ) -> Sequence[Device]:
        """Устройства конкретных адресатов в клинике (рассылка события)."""
        if not account_ids:
            return []
        rows = (
            await self._session.execute(
                select(Device).where(
                    Device.clinic_id == clinic_id, Device.account_id.in_(account_ids)
                )
            )
        ).scalars()
        return list(rows)

    async def delete_dead(self, token: str) -> None:
        """Удалить мёртвый токен (FCM/APNs ответили ``NotRegistered``) — безусловно.

        В отличие от снятия по logout, аккаунт здесь не проверяется:
        недействительный токен уже никому не принадлежит и должен уйти из таблицы
        целиком, кто бы его ни зарегистрировал последним.
        """
        await self._session.execute(delete(Device).where(Device.token == token))
        await self._session.flush()
