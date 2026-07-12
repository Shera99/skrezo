"""Репозиторий клиник (запись + проверка города)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import ClinicType, Lang
from app.shared.db.models.city import City
from app.shared.db.models.clinic import Clinic


class ClinicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def city_exists(self, city_id: uuid.UUID) -> bool:
        result = await self._session.execute(select(City.id).where(City.id == city_id))
        return result.scalar_one_or_none() is not None

    async def create(
        self,
        *,
        name: str,
        type: ClinicType,
        city_id: uuid.UUID | None,
        address: str | None,
        business_phone: str | None,
        seats_limit: int | None,
        lang: Lang,
    ) -> Clinic:
        clinic = Clinic(
            name=name,
            type=type,
            city_id=city_id,
            address=address,
            business_phone=business_phone,
            seats_limit=seats_limit,
            lang=lang,
        )
        self._session.add(clinic)
        await self._session.flush()
        return clinic
