"""Репозиторий настроек своей клиники: чтение карточки и правка разрешённых полей.

Работает только с клиникой из токена. Список правимых колонок задан явно: у
клиники есть поля, которыми владеет Skrezo (``type``, ``seats_limit`` — это
тариф) и внутренние (``support_notes`` — заметки саппорта). Они не отдаются
наружу и не правятся отсюда даже при попадании в тело запроса.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import Lang
from app.shared.db.models.city import City
from app.shared.db.models.city_translation import CityTranslation
from app.shared.db.models.clinic import Clinic
from app.shared.db.updates import apply_updates

# Что клиника вправе править о себе. Здесь НЕТ type/seats_limit (тариф Skrezo)
# и support_notes (внутренние заметки платформенной зоны).
_UPDATABLE_FIELDS = frozenset({"name", "city_id", "address", "business_phone", "lang", "timezone"})
# Из них NOT NULL в БД: NULL сюда ломает ленту дня (таймзона) и выдачи (имя, язык).
_NOT_NULL_FIELDS = frozenset({"name", "lang", "timezone"})


class ClinicSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, clinic_id: uuid.UUID) -> Clinic | None:
        return await self._session.get(Clinic, clinic_id)

    async def city_name(self, city_id: uuid.UUID | None, lang: Lang) -> str | None:
        """Название города на языке запроса — чтобы клиент не ходил в справочник."""
        if city_id is None:
            return None
        return (
            await self._session.execute(
                select(CityTranslation.value).where(
                    CityTranslation.city_id == city_id, CityTranslation.lang == lang
                )
            )
        ).scalar_one_or_none()

    async def city_exists(self, city_id: uuid.UUID) -> bool:
        return (
            await self._session.execute(select(City.id).where(City.id == city_id))
        ).scalar_one_or_none() is not None

    async def update(self, clinic: Clinic, fields: dict[str, Any]) -> None:
        """Записать разрешённые поля клиники."""
        apply_updates(
            clinic,
            fields,
            updatable=_UPDATABLE_FIELDS,
            not_null=_NOT_NULL_FIELDS,
            entity_name="клиники",
        )
        await self._session.flush()
