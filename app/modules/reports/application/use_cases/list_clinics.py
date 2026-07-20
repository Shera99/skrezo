"""Use-case: платформенный свод по всем клиникам (экран выбора клиники в Skrezo)."""

from __future__ import annotations

from datetime import date

from app.modules.reports.domain.models import ClinicsReport
from app.modules.reports.domain.period import ReportPeriod, resolve_period, resolve_tz
from app.modules.reports.infrastructure.repositories.reports_repository import ReportsRepository
from app.shared.db.enums import DEFAULT_LANG
from app.shared.db.models.clinic import DEFAULT_TIMEZONE


class ListClinicsReportUseCase:
    def __init__(self, reports: ReportsRepository) -> None:
        self._reports = reports

    async def execute(
        self,
        *,
        period: ReportPeriod | None,
        date_from: date | None,
        date_to: date | None,
    ) -> ClinicsReport:
        """Ключевые метрики по каждой клинике платформы за период.

        В отличие от отчётов конкретной клиники период здесь считается в
        **таймзоне платформы** (у клиник она может различаться, а свод — один
        общий срез); названия городов — на языке платформы по умолчанию.
        """
        bounds = resolve_period(period, date_from, date_to, resolve_tz(DEFAULT_TIMEZONE))
        rows = await self._reports.clinics(bounds.start, bounds.end, lang=DEFAULT_LANG)
        return ClinicsReport(
            date_from=bounds.date_from,
            date_to=bounds.date_to,
            rows=rows,
            total_revenue_net=sum(r.revenue_net for r in rows),
            total_debt=sum(r.debt for r in rows),
        )
