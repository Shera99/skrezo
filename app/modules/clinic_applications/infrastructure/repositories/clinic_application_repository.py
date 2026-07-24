"""Репозиторий заявок клиник: создание, список с фильтром, карточка, смена статуса.

Название города локализуется как в остальных справочниках:
``coalesce(язык платформы, ru)``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.clinic_applications.domain.models import ApplicationRow
from app.shared.db.enums import (
    DEFAULT_LANG,
    ClinicApplicationStatus,
    DoctorsCountRange,
    Lang,
)
from app.shared.db.models.account import Account
from app.shared.db.models.city import City
from app.shared.db.models.city_translation import CityTranslation
from app.shared.db.models.clinic_application import ClinicApplication


class ClinicApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def city_exists(self, city_id: uuid.UUID) -> bool:
        return (
            await self._session.execute(select(City.id).where(City.id == city_id))
        ).scalar_one_or_none() is not None

    async def create(
        self,
        *,
        company_name: str,
        city_id: uuid.UUID,
        doctors_count: DoctorsCountRange,
        phone: str,
    ) -> ClinicApplication:
        """Создать заявку в статусе ``pending``; коммит — на прикладном слое."""
        application = ClinicApplication(
            company_name=company_name,
            city_id=city_id,
            doctors_count=doctors_count,
            phone=phone,
            status=ClinicApplicationStatus.PENDING,
        )
        self._session.add(application)
        await self._session.flush()
        return application

    # ── Выборки ────────────────────────────────────────────────────────────
    def _row_query(self, lang: Lang) -> Select[Any]:
        tr_lang = aliased(CityTranslation)
        tr_ru = aliased(CityTranslation)
        reviewer = aliased(Account)
        return (
            select(
                ClinicApplication.id,
                ClinicApplication.company_name,
                ClinicApplication.city_id,
                func.coalesce(tr_lang.value, tr_ru.value).label("city_name"),
                ClinicApplication.doctors_count,
                ClinicApplication.phone,
                ClinicApplication.status,
                ClinicApplication.reviewed_by_account_id,
                reviewer.full_name.label("reviewed_by_name"),
                ClinicApplication.reviewed_at,
                ClinicApplication.created_at,
            )
            .outerjoin(City, City.id == ClinicApplication.city_id)
            .outerjoin(tr_lang, (tr_lang.city_id == City.id) & (tr_lang.lang == lang))
            .outerjoin(tr_ru, (tr_ru.city_id == City.id) & (tr_ru.lang == Lang.RU))
            .outerjoin(reviewer, reviewer.id == ClinicApplication.reviewed_by_account_id)
        )

    @staticmethod
    def _to_row(row: Any) -> ApplicationRow:
        return ApplicationRow(
            id=row[0],
            company_name=row[1],
            city_id=row[2],
            city_name=row[3],
            doctors_count=row[4],
            phone=row[5],
            status=row[6],
            reviewed_by_account_id=row[7],
            reviewed_by_name=row[8],
            reviewed_at=row[9],
            created_at=row[10],
        )

    async def list_page(
        self,
        *,
        status: ClinicApplicationStatus | None,
        limit: int,
        offset: int,
        lang: Lang = DEFAULT_LANG,
    ) -> tuple[list[ApplicationRow], int]:
        """Заявки (свежие сверху) с фильтром по статусу + общее число."""
        conditions: list[ColumnElement[bool]] = []
        if status is not None:
            conditions.append(ClinicApplication.status == status)

        total = (
            await self._session.execute(
                select(func.count()).select_from(ClinicApplication).where(*conditions)
            )
        ).scalar_one()
        rows = (
            await self._session.execute(
                self._row_query(lang)
                .where(*conditions)
                .order_by(ClinicApplication.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [self._to_row(r) for r in rows], int(total)

    async def get_row(
        self, application_id: uuid.UUID, lang: Lang = DEFAULT_LANG
    ) -> ApplicationRow | None:
        """Карточка заявки по id."""
        row = (
            await self._session.execute(
                self._row_query(lang).where(ClinicApplication.id == application_id)
            )
        ).first()
        return self._to_row(row) if row is not None else None

    async def set_status(
        self,
        application_id: uuid.UUID,
        status: ClinicApplicationStatus,
        actor_id: uuid.UUID,
    ) -> bool:
        """Сменить статус заявки; запомнить, кто и когда. Вернуть, найдена ли заявка."""
        application = await self._session.get(ClinicApplication, application_id)
        if application is None:
            return False
        application.status = status
        application.reviewed_by_account_id = actor_id
        application.reviewed_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def status_counts(self) -> dict[ClinicApplicationStatus, int]:
        """Число заявок по статусам — бейджи в админке."""
        rows = (
            await self._session.execute(
                select(ClinicApplication.status, func.count()).group_by(
                    ClinicApplication.status
                )
            )
        ).all()
        return {r[0]: int(r[1]) for r in rows}
