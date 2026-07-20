"""Use-case: топ услуг за период."""

from __future__ import annotations

import uuid
from datetime import date

from app.modules.reports.application.context import clinic_report_context
from app.modules.reports.domain.models import ServicesReport
from app.modules.reports.domain.period import ReportPeriod
from app.modules.reports.infrastructure.repositories.reports_repository import ReportsRepository


class GetServicesReportUseCase:
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
    ) -> ServicesReport:
        """Услуги по числу манипуляций и сумме, по убыванию выручки.

        Считается по ``appointment_tooth`` — фактически выполненным манипуляциям,
        а не по основной услуге приёма. Манипуляции без услуги сводятся в строку
        «Без услуги».
        """
        bounds, timezone, _ = await clinic_report_context(
            self._reports, clinic_id, period, date_from, date_to
        )
        rows = await self._reports.services(
            clinic_id, bounds.start, bounds.end, own_membership_id
        )
        return ServicesReport(timezone=timezone, rows=rows)
