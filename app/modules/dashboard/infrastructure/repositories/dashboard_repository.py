"""Репозиторий дашборда: агрегаты по клиникам/подпискам/лидам.

Метрики считаются как текущий срез (историческое версионирование срезов и MRR —
после подключения биллинга). Часть метрик (MRR, выручка) зависит от отложенного
биллинга и на этом слое не вычисляется.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.enums import ClinicType, SubscriptionStatus
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.lead import Lead
from app.shared.db.models.subscription import Subscription

# (created_at, тип клиники, статус подписки) — строка снапшота для трендов.
ClinicSnapshotRow = tuple[datetime, ClinicType, SubscriptionStatus | None]


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def counts_by_type(
        self, statuses: Sequence[SubscriptionStatus]
    ) -> dict[ClinicType, int]:
        """Число клиник с подпиской в указанных статусах, разбитое по типу."""
        rows = (
            await self._session.execute(
                select(Clinic.type, func.count())
                .select_from(Clinic)
                .join(Subscription, Subscription.clinic_id == Clinic.id)
                .where(Subscription.status.in_(statuses))
                .group_by(Clinic.type)
            )
        ).all()
        counts: dict[ClinicType, int] = dict.fromkeys(ClinicType, 0)
        for clinic_type, count in rows:
            counts[clinic_type] = count
        return counts

    async def status_counts(self) -> dict[SubscriptionStatus, int]:
        rows = (
            await self._session.execute(
                select(Subscription.status, func.count()).group_by(Subscription.status)
            )
        ).all()
        counts: dict[SubscriptionStatus, int] = dict.fromkeys(SubscriptionStatus, 0)
        for status, count in rows:
            counts[status] = count
        return counts

    async def trial_card_split(self) -> tuple[int, int]:
        """(всего триалов, из них с привязанной картой)."""
        total = (
            await self._session.execute(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.status == SubscriptionStatus.TRIAL)
            )
        ).scalar_one()
        with_card = (
            await self._session.execute(
                select(func.count())
                .select_from(Subscription)
                .where(
                    Subscription.status == SubscriptionStatus.TRIAL,
                    Subscription.card_attached_at.is_not(None),
                )
            )
        ).scalar_one()
        return total, with_card

    async def conversion(self) -> tuple[int, int]:
        """(всего лидов, из них сконвертированных в клинику)."""
        total = (
            await self._session.execute(select(func.count()).select_from(Lead))
        ).scalar_one()
        converted = (
            await self._session.execute(
                select(func.count())
                .select_from(Lead)
                .where(Lead.converted_clinic_id.is_not(None))
            )
        ).scalar_one()
        return total, converted

    async def clinics_snapshot(self) -> list[ClinicSnapshotRow]:
        """(created_at, type, статус подписки) по всем клиникам — для трендов."""
        rows = (
            await self._session.execute(
                select(Clinic.created_at, Clinic.type, Subscription.status).outerjoin(
                    Subscription, Subscription.clinic_id == Clinic.id
                )
            )
        ).all()
        return [(r[0], r[1], r[2]) for r in rows]

    async def lead_dates(self) -> list[datetime]:
        """Даты создания всех лидов — для помесячного тренда «Заявки»."""
        rows = (await self._session.execute(select(Lead.created_at))).scalars().all()
        return list(rows)
