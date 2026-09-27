"""Роут: GET /clinic/reports/revenue — динамика выручки (view_all/view_own)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.modules.reports.application.use_cases.get_revenue import (
    GetRevenueSeriesUseCase,
    RevenueGroupBy,
)
from app.modules.reports.presentation.access import (
    ReportsScope,
    require_reports_read,
    resolve_scope_doctor,
)
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_revenue_series_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import RevenueSeriesResponse

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {
        "description": "Чужой doctorMembershipId при праве только на своё (reports.view_own)",
        "content": {
            "application/json": {
                "example": {
                    "error": "not_own_report",
                    "message": "Доступны только свои показатели",
                }
            }
        },
    },
}


@router.get(
    "/clinic/reports/revenue",
    response_model=RevenueSeriesResponse,
    response_description="Точки временного ряда: приход, возвраты, нетто",
    summary="Динамика выручки",
    responses=_ERRORS,
)
async def revenue_series(
    group_by: RevenueGroupBy = Query(
        default=RevenueGroupBy.DAY, alias="groupBy", description="Шаг ряда: day | month"
    ),
    doctor_membership_id: uuid.UUID | None = Query(
        default=None,
        alias="doctorMembershipId",
        description="Только платежи по приёмам этого врача (id членства); с view_own — своё",
    ),
    period: PeriodQuery = Depends(period_query),
    scope: ReportsScope = Depends(require_reports_read),
    use_case: GetRevenueSeriesUseCase = Depends(get_revenue_series_use_case),
) -> RevenueSeriesResponse:
    """Выручка по дням или месяцам — по дате платежа, в таймзоне клиники.

    ``doctorMembershipId`` даёт ряд одного врача для его карточки. С правом
    ``reports.view_own`` учитываются только платежи по приёмам, где вызывающий
    лечащий врач; чужой ``doctorMembershipId`` при этом → 403 ``not_own_report``.
    """
    result = await use_case.execute(
        scope.clinic_id,
        period=period.period,
        date_from=period.date_from,
        date_to=period.date_to,
        group_by=group_by,
        doctor_membership_id=resolve_scope_doctor(scope, doctor_membership_id),
    )
    return RevenueSeriesResponse.from_series(result)
