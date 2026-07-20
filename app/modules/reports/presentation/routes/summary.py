"""Роут: GET /clinic/reports/summary — сводка отчётов (view_all → вся, view_own → своё)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.reports.application.use_cases.get_summary import GetReportSummaryUseCase
from app.modules.reports.presentation.access import ReportsScope, require_reports_read
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_report_summary_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import ReportSummaryResponse

router = APIRouter()


@router.get(
    "/clinic/reports/summary",
    response_model=ReportSummaryResponse,
    response_description="Деньги, приёмы, пациенты, долг и средний чек за период",
    summary="Сводка отчётов клиники",
)
async def report_summary(
    period: PeriodQuery = Depends(period_query),
    scope: ReportsScope = Depends(require_reports_read),
    use_case: GetReportSummaryUseCase = Depends(get_report_summary_use_case),
) -> ReportSummaryResponse:
    """Всё для экрана отчётов одним запросом.

    Деньги считаются по дате платежа, приёмы и производное от них — по дате
    приёма. С правом ``reports.view_all`` — по всей клинике, с ``reports.view_own``
    все разрезы сужены до приёмов, где вызывающий лечащий врач.
    """
    result = await use_case.execute(
        scope.clinic_id,
        period=period.period,
        date_from=period.date_from,
        date_to=period.date_to,
        own_membership_id=scope.own_membership_id,
    )
    return ReportSummaryResponse.from_summary(result)
