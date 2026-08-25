"""Use-case: изменить настройки уведомлений (отключённые типы, тихие часы)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.push.infrastructure.repositories.notification_pref_repository import (
    NotificationPrefRepository,
)
from app.shared.db.models.notification_pref import NotificationPref


class UpdatePrefsUseCase:
    def __init__(self, session: AsyncSession, prefs: NotificationPrefRepository) -> None:
        self._session = session
        self._prefs = prefs

    async def execute(
        self, account_id: uuid.UUID, clinic_id: uuid.UUID, fields: dict[str, Any]
    ) -> NotificationPref:
        pref = await self._prefs.upsert(account_id, clinic_id, fields)
        await self._session.commit()
        return pref
