"""Роут: GET /admin/reports/clinics/{id}/revenue — динамика выручки клиники (Skrezo)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.reports.application.use_cases.get_revenue import (
    GetRevenueSeriesUseCase,
    RevenueGroupBy,
)
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_revenue_series_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import RevenueSeriesResponse

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Клиника не найдена",
        "content": {
            "application/json": {
                "example": {"error": "clinic_not_found", "message": "Клиника не найдена"}
            }
        },
    },
}


@router.get(
    "/admin/reports/clinics/{clinic_id}/revenue",
    response_model=RevenueSeriesResponse,
    response_description="Точки временного ряда: приход, возвраты, нетто",
    summary="Динамика выручки выбранной клиники",
    responses=_ERRORS,
)
async def admin_clinic_revenue(
    clinic_id: uuid.UUID,
    group_by: RevenueGroupBy = Query(
        default=RevenueGroupBy.DAY, alias="groupBy", description="Шаг ряда: day | month"
    ),
    doctor_membership_id: uuid.UUID | None = Query(
        default=None,
        alias="doctorMembershipId",
        description="Только платежи по приёмам этого врача (id членства)",
    ),
    period: PeriodQuery = Depends(period_query),
    _: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetRevenueSeriesUseCase = Depends(get_revenue_series_use_case),
) -> RevenueSeriesResponse:
    """Выручка выбранной клиники по дням или месяцам (по дате платежа), при
    ``doctorMembershipId`` — одного её врача."""
    result = await use_case.execute(
        clinic_id,
        period=period.period,
        date_from=period.date_from,
        date_to=period.date_to,
        group_by=group_by,
        doctor_membership_id=doctor_membership_id,
    )
    return RevenueSeriesResponse.from_series(result)
