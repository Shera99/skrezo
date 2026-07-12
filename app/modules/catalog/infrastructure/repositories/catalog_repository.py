"""Репозиторий справочников: доступ к данным city/specialization + переводы.

Инкапсулирует SQL: чтение с локализацией (``coalesce(язык, ru)``). ORM-модели
берутся из общего слоя ``app/shared/db/models``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.shared.db.enums import Lang
from app.shared.db.models.city import City
from app.shared.db.models.city_translation import CityTranslation
from app.shared.db.models.specialization import Specialization
from app.shared.db.models.specialization_translation import SpecializationTranslation


class CatalogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_cities(self, lang: Lang) -> list[dict[str, Any]]:
        return await self._list(City, CityTranslation, "city_id", lang)

    async def list_specializations(self, lang: Lang) -> list[dict[str, Any]]:
        return await self._list(
            Specialization, SpecializationTranslation, "specialization_id", lang
        )

    async def _list(
        self,
        base: type[City] | type[Specialization],
        translation: type[CityTranslation] | type[SpecializationTranslation],
        fk_name: str,
        lang: Lang,
    ) -> list[dict[str, Any]]:
        tr_lang = aliased(translation)
        tr_ru = aliased(translation)
        stmt = (
            select(base.id, func.coalesce(tr_lang.value, tr_ru.value).label("name"))
            .join(
                tr_lang,
                (getattr(tr_lang, fk_name) == base.id) & (tr_lang.lang == lang),
                isouter=True,
            )
            .join(
                tr_ru,
                (getattr(tr_ru, fk_name) == base.id) & (tr_ru.lang == Lang.RU),
                isouter=True,
            )
            .where(base.active.is_(True))
            .order_by(base.sort_order)
        )
        rows = (await self._session.execute(stmt)).all()
        return [{"id": str(row.id), "name": row.name} for row in rows]
