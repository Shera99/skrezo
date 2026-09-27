"""Роут: GET /admin/reports/clinics/{id}/doctors — врачи клиники (только Skrezo)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.reports.application.use_cases.get_doctors import GetDoctorsReportUseCase
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_doctors_report_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import DoctorsReportResponse

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
    "/admin/reports/clinics/{clinic_id}/doctors",
    response_model=DoctorsReportResponse,
    response_description=(
        "Строка на врача: приёмы, неявки, начислено, оплачено, долг, средний чек, загрузка"
    ),
    summary="Отчёт по врачам выбранной клиники",
    responses=_ERRORS,
)
async def admin_clinic_doctors(
    clinic_id: uuid.UUID,
    period: PeriodQuery = Depends(period_query),
    _: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetDoctorsReportUseCase = Depends(get_doctors_report_use_case),
) -> DoctorsReportResponse:
    """Все врачи выбранной клиники за период, по убыванию начисленного."""
    result = await use_case.execute(
        clinic_id,
        period=period.period,
        date_from=period.date_from,
        date_to=period.date_to,
        own_membership_id=None,
    )
    return DoctorsReportResponse.from_report(result)
