"""Use-case: сводка дашборда за период.

Реальные метрики (активные/триал/отмены/конверсия) считаются из БД как текущий
срез. MRR/выручка и дельты к прошлому периоду зависят от отложенного биллинга и
историзации срезов — пока нули.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.dashboard.infrastructure.repositories.dashboard_repository import (
    DashboardRepository,
)
from app.shared.db.enums import ClinicType, SubscriptionStatus

# «Активные» клиенты = актуально пользуются (триал или оплаченные).
_ACTIVE_STATUSES = (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE)


@dataclass(slots=True)
class DashboardData:
    active: dict[ClinicType, int]
    cancelled: dict[ClinicType, int]
    status_counts: dict[SubscriptionStatus, int]
    trial_total: int
    trial_with_card: int
    leads_total: int
    converted: int


class GetSummaryUseCase:
    def __init__(self, dashboard: DashboardRepository) -> None:
        self._dashboard = dashboard

    async def execute(self) -> DashboardData:
        active = await self._dashboard.counts_by_type(_ACTIVE_STATUSES)
        cancelled = await self._dashboard.counts_by_type((SubscriptionStatus.CANCELLED,))
        status_counts = await self._dashboard.status_counts()
        trial_total, trial_with_card = await self._dashboard.trial_card_split()
        leads_total, converted = await self._dashboard.conversion()
        return DashboardData(
            active=active,
            cancelled=cancelled,
            status_counts=status_counts,
            trial_total=trial_total,
            trial_with_card=trial_with_card,
            leads_total=leads_total,
            converted=converted,
        )
