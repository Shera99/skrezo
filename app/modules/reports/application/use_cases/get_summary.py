"""Use-case: сводка отчётов клиники за период (всё для экрана одним запросом)."""

from __future__ import annotations

import uuid
from datetime import date

from app.modules.reports.application.context import clinic_report_context
from app.modules.reports.domain.models import PatientsBlock, ReportSummary
from app.modules.reports.domain.period import ReportPeriod
from app.modules.reports.infrastructure.repositories.reports_repository import ReportsRepository


class GetReportSummaryUseCase:
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
    ) -> ReportSummary:
        """Деньги, приёмы, пациенты и долг за период.

        ``own_membership_id`` (``reports.view_own``) сужает все разрезы до приёмов,
        где вызывающий — лечащий врач.
        """
        bounds, timezone, _ = await clinic_report_context(
            self._reports, clinic_id, period, date_from, date_to
        )
        revenue = await self._reports.revenue(
            clinic_id, bounds.start, bounds.end, own_membership_id
        )
        appointments = await self._reports.appointments_by_status(
            clinic_id, bounds.start, bounds.end, own_membership_id
        )
        charged, paid, completed, discounts = await self._reports.charged_and_paid(
            clinic_id, bounds.start, bounds.end, own_membership_id
        )
        total_patients, new_patients = await self._reports.patients(
            clinic_id, bounds.start, bounds.end, own_membership_id
        )
        return ReportSummary(
            timezone=timezone,
            date_from=bounds.date_from,
            date_to=bounds.date_to,
            revenue=revenue,
            appointments=appointments,
            patients=PatientsBlock(
                total=total_patients,
                new=new_patients,
                returning=total_patients - new_patients,
            ),
            charged_tiyin=charged,
            discounts_tiyin=discounts,
            debt_tiyin=charged - paid,
            average_check_tiyin=charged // completed if completed else 0,
        )
