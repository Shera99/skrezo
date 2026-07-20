"""Use-case: динамика выручки по дням или месяцам."""

from __future__ import annotations

import enum
import uuid
from datetime import date

from app.modules.reports.application.context import clinic_report_context
from app.modules.reports.domain.models import RevenueSeries
from app.modules.reports.domain.period import ReportPeriod
from app.modules.reports.infrastructure.repositories.reports_repository import ReportsRepository


class RevenueGroupBy(enum.StrEnum):
    """Шаг временного ряда выручки."""

    DAY = "day"
    MONTH = "month"


class GetRevenueSeriesUseCase:
    def __init__(self, reports: ReportsRepository) -> None:
        self._reports = reports

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        period: ReportPeriod | None,
        date_from: date | None,
        date_to: date | None,
        group_by: RevenueGroupBy,
        own_membership_id: uuid.UUID | None,
    ) -> RevenueSeries:
        """Выручка по дням/месяцам (по дате платежа, в таймзоне клиники)."""
        bounds, timezone, _ = await clinic_report_context(
            self._reports, clinic_id, period, date_from, date_to
        )
        points = await self._reports.revenue_series(
            clinic_id,
            bounds.start,
            bounds.end,
            own_membership_id,
            group_by=group_by.value,
            timezone=timezone,
        )
        return RevenueSeries(timezone=timezone, group_by=group_by.value, points=points)
