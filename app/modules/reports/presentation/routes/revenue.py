"""Роут: GET /clinic/reports/revenue — динамика выручки (view_all/view_own)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.modules.reports.application.use_cases.get_revenue import (
    GetRevenueSeriesUseCase,
    RevenueGroupBy,
)
from app.modules.reports.presentation.access import ReportsScope, require_reports_read
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_revenue_series_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import RevenueSeriesResponse

router = APIRouter()


@router.get(
    "/clinic/reports/revenue",
    response_model=RevenueSeriesResponse,
    response_description="Точки временного ряда: приход, возвраты, нетто",
    summary="Динамика выручки",
)
async def revenue_series(
    group_by: RevenueGroupBy = Query(
        default=RevenueGroupBy.DAY, alias="groupBy", description="Шаг ряда: day | month"
    ),
    period: PeriodQuery = Depends(period_query),
    scope: ReportsScope = Depends(require_reports_read),
    use_case: GetRevenueSeriesUseCase = Depends(get_revenue_series_use_case),
) -> RevenueSeriesResponse:
    """Выручка по дням или месяцам — по дате платежа, в таймзоне клиники.

    С правом ``reports.view_own`` учитываются только платежи по приёмам, где
    вызывающий лечащий врач.
    """
    result = await use_case.execute(
        scope.clinic_id,
        period=period.period,
        date_from=period.date_from,
        date_to=period.date_to,
        group_by=group_by,
        own_membership_id=scope.own_membership_id,
    )
    return RevenueSeriesResponse.from_series(result)
