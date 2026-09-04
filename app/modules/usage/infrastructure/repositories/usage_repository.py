"""Репозиторий счётчиков использования: инкременты и выборки по периодам."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.domain.rates import UsageTotals
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_usage_day import USAGE_COUNTERS, ClinicUsageDay


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, clinic_id: uuid.UUID, day: date, **increments: int) -> None:
        """Прибавить к счётчикам дня; строки нет — создать. Один запрос, без гонок."""
        increments = {k: int(v) for k, v in increments.items() if k in USAGE_COUNTERS and v}
        if not increments:
            return
        stmt = pg_insert(ClinicUsageDay).values(
            id=uuid.uuid4(), clinic_id=clinic_id, day=day, **increments
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_clinic_usage_day",
            set_={
                **{k: getattr(ClinicUsageDay, k) + stmt.excluded[k] for k in increments},
                "updated_at": func.now(),
            },
        )
        await self._session.execute(stmt)

    async def daily(
        self, clinic_id: uuid.UUID, date_from: date, date_to: date
    ) -> list[ClinicUsageDay]:
        rows = (
            await self._session.execute(
                select(ClinicUsageDay)
                .where(
                    ClinicUsageDay.clinic_id == clinic_id,
                    ClinicUsageDay.day >= date_from,
                    ClinicUsageDay.day <= date_to,
                )
                .order_by(ClinicUsageDay.day)
            )
        ).scalars()
        return list(rows)

    async def totals_by_clinic(
        self, date_from: date, date_to: date
    ) -> list[tuple[uuid.UUID, str, UsageTotals]]:
        """Суммы за период по каждой клинике, где было хоть что-то."""
        sums = [func.sum(getattr(ClinicUsageDay, name)).label(name) for name in USAGE_COUNTERS]
        rows = (
            await self._session.execute(
                select(ClinicUsageDay.clinic_id, Clinic.name, *sums)
                .join(Clinic, Clinic.id == ClinicUsageDay.clinic_id)
                .where(ClinicUsageDay.day >= date_from, ClinicUsageDay.day <= date_to)
                .group_by(ClinicUsageDay.clinic_id, Clinic.name)
                .order_by(Clinic.name)
            )
        ).all()
        return [
            (
                row[0],
                row[1],
                UsageTotals(
                    **{name: int(row[2 + i] or 0) for i, name in enumerate(USAGE_COUNTERS)}
                ),
            )
            for row in rows
        ]
