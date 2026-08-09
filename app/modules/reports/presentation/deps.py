"""Зависимости модуля отчётов."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.reports.application.use_cases.get_day_dashboard import (
    GetDayDashboardUseCase,
)
from app.modules.reports.application.use_cases.get_doctors import GetDoctorsReportUseCase
from app.modules.reports.application.use_cases.get_revenue import GetRevenueSeriesUseCase
from app.modules.reports.application.use_cases.get_services import GetServicesReportUseCase
from app.modules.reports.application.use_cases.get_summary import GetReportSummaryUseCase
from app.modules.reports.application.use_cases.list_clinics import ListClinicsReportUseCase
from app.modules.reports.domain.period import ReportPeriod
from app.modules.reports.infrastructure.repositories.reports_repository import ReportsRepository


@dataclass(frozen=True, slots=True)
class PeriodQuery:
    """Разобранные параметры периода отчёта."""

    period: ReportPeriod | None
    date_from: date | None
    date_to: date | None


def period_query(
    period: ReportPeriod | None = Query(
        default=None,
        description="Пресет периода: 7d | 1m | 3m | all (по умолчанию 1m)",
        examples=["1m"],
    ),
    date_from: date | None = Query(
        default=None, alias="from", description="Начало произвольного периода (перекрывает пресет)"
    ),
    date_to: date | None = Query(
        default=None, alias="to", description="Конец периода включительно"
    ),
) -> PeriodQuery:
    """Период отчёта: пресет либо произвольный диапазон ``from``/``to``.

    Явные даты приоритетнее пресета. Границы считаются в таймзоне клиники.
    """
    return PeriodQuery(period=period, date_from=date_from, date_to=date_to)


def get_reports_repository(session: AsyncSession = Depends(get_session)) -> ReportsRepository:
    return ReportsRepository(session)


def get_report_summary_use_case(
    reports: ReportsRepository = Depends(get_reports_repository),
) -> GetReportSummaryUseCase:
    return GetReportSummaryUseCase(reports)


def get_revenue_series_use_case(
    reports: ReportsRepository = Depends(get_reports_repository),
) -> GetRevenueSeriesUseCase:
    return GetRevenueSeriesUseCase(reports)


def get_doctors_report_use_case(
    reports: ReportsRepository = Depends(get_reports_repository),
) -> GetDoctorsReportUseCase:
    return GetDoctorsReportUseCase(reports)


def get_services_report_use_case(
    reports: ReportsRepository = Depends(get_reports_repository),
) -> GetServicesReportUseCase:
    return GetServicesReportUseCase(reports)


def get_list_clinics_report_use_case(
    reports: ReportsRepository = Depends(get_reports_repository),
) -> ListClinicsReportUseCase:
    return ListClinicsReportUseCase(reports)


def get_day_dashboard_use_case(
    reports: ReportsRepository = Depends(get_reports_repository),
) -> GetDayDashboardUseCase:
    return GetDayDashboardUseCase(reports)
