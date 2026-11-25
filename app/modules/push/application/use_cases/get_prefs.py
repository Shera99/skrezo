"""Use-case: прочитать настройки уведомлений своего членства."""

from __future__ import annotations

import uuid

from app.modules.push.infrastructure.repositories.notification_pref_repository import (
    NotificationPrefRepository,
)
from app.shared.db.models.notification_pref import NotificationPref


class GetPrefsUseCase:
    def __init__(self, prefs: NotificationPrefRepository) -> None:
        self._prefs = prefs

    async def execute(self, account_id: uuid.UUID, clinic_id: uuid.UUID) -> NotificationPref | None:
        """Строка настроек пары (аккаунт, клиника).

        ``None`` — настройки ни разу не правили: строки в БД нет, действует
        дефолт. Заводить её чтением незачем — это сделает первая правка.
        """
        return await self._prefs.get(account_id, clinic_id)
