"""Use-case: тренды дашборда по месяцам (для графиков).

Серии считаются из реальных данных, где возможно: «Заявки→Клиенты» и «Активные
клиенты» — по `lead.created_at` / `clinic.created_at`; соотношение solo/clinic —
текущий срез. **MRR** зависит от отложенного биллинга → нули. «Активные по
месяцам» — накопительно (клиники, созданные ≤ конца месяца, кроме cancelled) по
ТЕКУЩЕМУ статусу (историзации срезов ещё нет).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.modules.dashboard.infrastructure.repositories.dashboard_repository import (
    DashboardRepository,
)
from app.shared.db.enums import ClinicType, SubscriptionStatus


@dataclass(slots=True)
class MonthBucket:
    key: str
    start: datetime
    end: datetime


@dataclass(slots=True)
class TrendsData:
    months: list[str] = field(default_factory=list)
    mrr: list[int] = field(default_factory=list)
    active_solo: list[int] = field(default_factory=list)
    active_clinic: list[int] = field(default_factory=list)
    leads: list[int] = field(default_factory=list)
    new_clients: list[int] = field(default_factory=list)
    ratio_solo: int = 0
    ratio_clinic: int = 0


def _month_buckets(now: datetime, months: int) -> list[MonthBucket]:
    """Список из ``months`` последних месяцев, заканчивая текущим."""
    year, month = now.year, now.month
    pairs: list[tuple[int, int]] = []
    for _ in range(months):
        pairs.append((year, month))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    pairs.reverse()

    buckets: list[MonthBucket] = []
    for yy, mm in pairs:
        start = datetime(yy, mm, 1, tzinfo=UTC)
        end = (
            datetime(yy + 1, 1, 1, tzinfo=UTC)
            if mm == 12
            else datetime(yy, mm + 1, 1, tzinfo=UTC)
        )
        buckets.append(MonthBucket(key=f"{yy:04d}-{mm:02d}", start=start, end=end))
    return buckets


class GetTrendsUseCase:
    def __init__(self, dashboard: DashboardRepository) -> None:
        self._dashboard = dashboard

    async def execute(self, *, months: int, now: datetime) -> TrendsData:
        clinics = await self._dashboard.clinics_snapshot()
        lead_dates = await self._dashboard.lead_dates()
        buckets = _month_buckets(now, months)

        data = TrendsData(months=[b.key for b in buckets])
        for b in buckets:
            data.mrr.append(0)  # биллинг отложен
            new_solo = sum(
                1 for c, t, _ in clinics if t is ClinicType.SOLO and b.start <= c < b.end
            )
            new_clinic = sum(
                1 for c, t, _ in clinics if t is ClinicType.CLINIC and b.start <= c < b.end
            )
            data.new_clients.append(new_solo + new_clinic)
            data.leads.append(sum(1 for d in lead_dates if b.start <= d < b.end))
            # Накопительно на конец месяца, кроме отменённых (по текущему статусу).
            data.active_solo.append(
                sum(
                    1
                    for c, t, s in clinics
                    if t is ClinicType.SOLO
                    and c < b.end
                    and s is not SubscriptionStatus.CANCELLED
                )
            )
            data.active_clinic.append(
                sum(
                    1
                    for c, t, s in clinics
                    if t is ClinicType.CLINIC
                    and c < b.end
                    and s is not SubscriptionStatus.CANCELLED
                )
            )

        data.ratio_solo = sum(
            1
            for _, t, s in clinics
            if t is ClinicType.SOLO and s is not SubscriptionStatus.CANCELLED
        )
        data.ratio_clinic = sum(
            1
            for _, t, s in clinics
            if t is ClinicType.CLINIC and s is not SubscriptionStatus.CANCELLED
        )
        return data
