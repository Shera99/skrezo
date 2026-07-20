"""Роут: GET /admin/reports/clinics/{id}/services — услуги клиники (только Skrezo)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.reports.application.use_cases.get_services import GetServicesReportUseCase
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_services_report_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import ServicesReportResponse

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
    "/admin/reports/clinics/{clinic_id}/services",
    response_model=ServicesReportResponse,
    response_description="Услуги по числу манипуляций и выручке",
    summary="Топ услуг выбранной клиники",
    responses=_ERRORS,
)
async def admin_clinic_services(
    clinic_id: uuid.UUID,
    period: PeriodQuery = Depends(period_query),
    _: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetServicesReportUseCase = Depends(get_services_report_use_case),
) -> ServicesReportResponse:
    """Услуги выбранной клиники за период по убыванию выручки."""
    result = await use_case.execute(
        clinic_id,
        period=period.period,
        date_from=period.date_from,
        date_to=period.date_to,
        own_membership_id=None,
    )
    return ServicesReportResponse.from_report(result)
