"""Роут: GET /clinic/reports/services — топ услуг (view_all/view_own)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.reports.application.use_cases.get_services import GetServicesReportUseCase
from app.modules.reports.presentation.access import ReportsScope, require_reports_read
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_services_report_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import ServicesReportResponse

router = APIRouter()


@router.get(
    "/clinic/reports/services",
    response_model=ServicesReportResponse,
    response_description="Услуги по числу манипуляций и выручке",
    summary="Топ услуг",
)
async def services_report(
    period: PeriodQuery = Depends(period_query),
    scope: ReportsScope = Depends(require_reports_read),
    use_case: GetServicesReportUseCase = Depends(get_services_report_use_case),
) -> ServicesReportResponse:
    """Услуги за период по убыванию выручки.

    Считается по позициям приёмов (``appointment_item``) с учётом количества, а
    не по основной услуге приёма; позиции без услуги сводятся в «Без услуги».
    С правом ``reports.view_own`` — только по своим приёмам.
    """
    result = await use_case.execute(
        scope.clinic_id,
        period=period.period,
        date_from=period.date_from,
        date_to=period.date_to,
        own_membership_id=scope.own_membership_id,
    )
    return ServicesReportResponse.from_report(result)
