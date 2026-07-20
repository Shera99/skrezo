"""Роут: GET /admin/reports/clinics/{id}/summary — сводка по клинике (только Skrezo)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.reports.application.use_cases.get_summary import GetReportSummaryUseCase
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_report_summary_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import ReportSummaryResponse

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
    "/admin/reports/clinics/{clinic_id}/summary",
    response_model=ReportSummaryResponse,
    response_description="Деньги, приёмы, пациенты, долг и средний чек за период",
    summary="Сводка отчётов выбранной клиники",
    responses=_ERRORS,
)
async def admin_clinic_summary(
    clinic_id: uuid.UUID,
    period: PeriodQuery = Depends(period_query),
    _: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetReportSummaryUseCase = Depends(get_report_summary_use_case),
) -> ReportSummaryResponse:
    """Та же сводка, что видит клиника, но по любой клинике платформы.

    Ограничений по врачу нет: Skrezo видит полный срез. Период считается в
    таймзоне выбранной клиники.
    """
    result = await use_case.execute(
        clinic_id,
        period=period.period,
        date_from=period.date_from,
        date_to=period.date_to,
        own_membership_id=None,
    )
    return ReportSummaryResponse.from_summary(result)
