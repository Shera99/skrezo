"""Репозиторий префов уведомлений: чтение и правка настроек на членство."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.notification_pref import NotificationPref
from app.shared.db.updates import apply_updates

# Что клиент вправе менять в своих префах.
_UPDATABLE_FIELDS = frozenset({"disabled", "quiet_from", "quiet_to"})
# Из них NOT NULL в БД: пустой список — легитимное значение «ничего не
# отключено», а вот сам список отсутствовать (NULL) не должен.
_NOT_NULL_FIELDS = frozenset({"disabled"})


class NotificationPrefRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, account_id: uuid.UUID, clinic_id: uuid.UUID) -> NotificationPref | None:
        """Строка префов членства; ``None`` — не заводили, всё по умолчанию."""
        return (
            await self._session.execute(
                select(NotificationPref).where(
                    NotificationPref.account_id == account_id,
                    NotificationPref.clinic_id == clinic_id,
                )
            )
        ).scalar_one_or_none()

    async def upsert(
        self, account_id: uuid.UUID, clinic_id: uuid.UUID, fields: dict[str, Any]
    ) -> NotificationPref:
        """Завести строку префов при первой правке либо обновить существующую."""
        pref = await self.get(account_id, clinic_id)
        if pref is None:
            pref = NotificationPref(account_id=account_id, clinic_id=clinic_id)
            self._session.add(pref)
            await self._session.flush()
        apply_updates(
            pref,
            fields,
            updatable=_UPDATABLE_FIELDS,
            not_null=_NOT_NULL_FIELDS,
            entity_name="настроек уведомлений",
        )
        await self._session.flush()
        return pref
