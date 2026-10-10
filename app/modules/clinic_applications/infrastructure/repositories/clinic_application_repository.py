"""Репозиторий заявок клиник: создание, список с фильтром, карточка, смена статуса.

Очередь одна на две двери: заявки через OTP (``phone_verified = true``) и лиды с
лендинга (``phone_verified = false``).

Название города локализуется как в остальных справочниках:
``coalesce(язык платформы, ru)``. У лида города нет — соединение внешнее.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, func, select, update
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

    async def account_exists_by_phone(self, phone: str) -> bool:
        """Есть ли по телефону аккаунт (телефон уникален по платформе)."""
        return (
            await self._session.execute(select(Account.id).where(Account.phone == phone))
        ).scalar_one_or_none() is not None

    async def last_pending_created_at(self, phone: str) -> datetime | None:
        """Когда создана самая свежая открытая заявка по телефону (любой двери).

        ``None`` — открытых (``pending``) заявок по номеру нет. Разобранные
        (``reviewed``/``rejected``) в счёт не идут: человек вправе прийти снова.
        """
        return (
            await self._session.execute(
                select(func.max(ClinicApplication.created_at)).where(
                    ClinicApplication.phone == phone,
                    ClinicApplication.status == ClinicApplicationStatus.PENDING,
                )
            )
        ).scalar_one_or_none()

    async def find_other_open_lead_id(self, application_id: uuid.UUID) -> uuid.UUID | None:
        """Id другого открытого лида с тем же телефоном, что у заявки ``application_id``.

        Нужен, когда возврат заявки в ``pending`` упёрся в «один открытый лид на
        номер»: админу показывают, какой лид надо разобрать первым. ``None`` —
        такого лида нет (например, его разобрали параллельным запросом).
        """
        other = aliased(ClinicApplication)
        return (
            await self._session.execute(
                select(other.id)
                .join(ClinicApplication, ClinicApplication.phone == other.phone)
                .where(
                    ClinicApplication.id == application_id,
                    other.id != application_id,
                    other.status == ClinicApplicationStatus.PENDING,
                    other.phone_verified.is_(False),
                )
                .order_by(other.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def create(
        self,
        *,
        phone: str,
        phone_verified: bool,
        company_name: str | None = None,
        city_id: uuid.UUID | None = None,
        doctors_count: DoctorsCountRange | None = None,
        full_name: str | None = None,
        comment: str | None = None,
        source: str | None = None,
    ) -> ClinicApplication:
        """Создать заявку в статусе ``pending``; коммит — на прикладном слое.

        ``phone_verified`` обязателен без значения по умолчанию: каждая дверь
        обязана сказать, подтверждён ли телефон. Целостность набора полей
        (полная заявка / лид с именем) дополнительно держат CHECK-и таблицы.
        """
        application = ClinicApplication(
            phone=phone,
            phone_verified=phone_verified,
            company_name=company_name,
            city_id=city_id,
            doctors_count=doctors_count,
            full_name=full_name,
            comment=comment,
            source=source,
            status=ClinicApplicationStatus.PENDING,
        )
        self._session.add(application)
        await self._session.flush()
        return application

    async def mark_converted(self, application_id: uuid.UUID, clinic_id: uuid.UUID) -> None:
        """Отметить, что по заявке заведена клиника (конверсия для дашборда)."""
        await self._session.execute(
            update(ClinicApplication)
            .where(ClinicApplication.id == application_id)
            .values(converted_clinic_id=clinic_id)
        )
        await self._session.flush()

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
                ClinicApplication.full_name,
                ClinicApplication.phone,
                ClinicApplication.phone_verified,
                ClinicApplication.source,
                ClinicApplication.comment,
                ClinicApplication.status,
                ClinicApplication.reviewed_by_account_id,
                reviewer.full_name.label("reviewed_by_name"),
                ClinicApplication.reviewed_at,
                ClinicApplication.created_at,
            )
            # Город у лида пустой → все соединения внешние, строка не теряется.
            .outerjoin(City, City.id == ClinicApplication.city_id)
            .outerjoin(tr_lang, (tr_lang.city_id == City.id) & (tr_lang.lang == lang))
            .outerjoin(tr_ru, (tr_ru.city_id == City.id) & (tr_ru.lang == Lang.RU))
            .outerjoin(reviewer, reviewer.id == ClinicApplication.reviewed_by_account_id)
        )

    @staticmethod
    def _to_row(row: Any) -> ApplicationRow:
        return ApplicationRow(
            id=row.id,
            company_name=row.company_name,
            city_id=row.city_id,
            city_name=row.city_name,
            doctors_count=row.doctors_count,
            full_name=row.full_name,
            phone=row.phone,
            phone_verified=row.phone_verified,
            source=row.source,
            comment=row.comment,
            status=row.status,
            reviewed_by_account_id=row.reviewed_by_account_id,
            reviewed_by_name=row.reviewed_by_name,
            reviewed_at=row.reviewed_at,
            created_at=row.created_at,
        )

    async def list_page(
        self,
        *,
        status: ClinicApplicationStatus | None,
        phone_verified: bool | None,
        limit: int,
        offset: int,
        lang: Lang = DEFAULT_LANG,
    ) -> tuple[list[ApplicationRow], int]:
        """Заявки (свежие сверху) с фильтрами по статусу и дверям + общее число."""
        conditions: list[ColumnElement[bool]] = []
        if status is not None:
            conditions.append(ClinicApplication.status == status)
        if phone_verified is not None:
            conditions.append(ClinicApplication.phone_verified == phone_verified)

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

    async def status_counts(
        self, *, phone_verified: bool | None = None
    ) -> dict[ClinicApplicationStatus, int]:
        """Число заявок по статусам — бейджи в админке.

        Фильтр по статусу сюда намеренно не входит (бейджи показывают все
        статусы сразу), а фильтр по ``phone_verified`` учитывается — чтобы
        бейджи совпадали со списком, отфильтрованным по двери.
        """
        query = select(ClinicApplication.status, func.count()).group_by(ClinicApplication.status)
        if phone_verified is not None:
            query = query.where(ClinicApplication.phone_verified == phone_verified)
        rows = (await self._session.execute(query)).all()
        return {r[0]: int(r[1]) for r in rows}
