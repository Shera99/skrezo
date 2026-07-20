"""Use-case: отчёт по врачам за период."""

from __future__ import annotations

import uuid
from datetime import date

from app.modules.reports.application.context import clinic_report_context
from app.modules.reports.domain.models import DoctorsReport
from app.modules.reports.domain.period import ReportPeriod
from app.modules.reports.infrastructure.repositories.reports_repository import ReportsRepository


class GetDoctorsReportUseCase:
    def __init__(self, reports: ReportsRepository) -> None:
        self._reports = reports

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        period: ReportPeriod | None,
        date_from: date | None,
        date_to: date | None,
        own_membership_id: uuid.UUID | None,
    ) -> DoctorsReport:
        """Приёмы, деньги и долг по каждому врачу.

        С правом ``reports.view_own`` в выдаче останется одна строка — сам
        вызывающий: разрезать чужие показатели врачу незачем.
        """
        bounds, timezone, lang = await clinic_report_context(
            self._reports, clinic_id, period, date_from, date_to
        )
        rows = await self._reports.doctors(
            clinic_id, bounds.start, bounds.end, own_membership_id, lang=lang
        )
        return DoctorsReport(timezone=timezone, rows=rows)
