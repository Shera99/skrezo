"""Роут: GET /clinic/reports/services — топ услуг (view_all/view_own)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.modules.reports.application.use_cases.get_services import GetServicesReportUseCase
from app.modules.reports.presentation.access import (
    ReportsScope,
    require_reports_read,
    resolve_scope_doctor,
)
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_services_report_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import ServicesReportResponse

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
    "/clinic/reports/services",
    response_model=ServicesReportResponse,
    response_description="Услуги по числу манипуляций и выручке",
    summary="Топ услуг",
    responses=_ERRORS,
)
async def services_report(
    doctor_membership_id: uuid.UUID | None = Query(
        default=None,
        alias="doctorMembershipId",
        description="Только позиции приёмов этого врача (id членства); с view_own — своё",
    ),
    period: PeriodQuery = Depends(period_query),
    scope: ReportsScope = Depends(require_reports_read),
    use_case: GetServicesReportUseCase = Depends(get_services_report_use_case),
) -> ServicesReportResponse:
    """Услуги за период по убыванию выручки.

    Считается по позициям приёмов (``appointment_item``) с учётом количества, а
    не по основной услуге приёма; позиции без услуги сводятся в «Без услуги».
    ``doctorMembershipId`` даёт услуги одного врача для его карточки. С правом
    ``reports.view_own`` — только по своим приёмам; чужой ``doctorMembershipId``
    при этом → 403 ``not_own_report``.
    """
    result = await use_case.execute(
        scope.clinic_id,
        period=period.period,
        date_from=period.date_from,
        date_to=period.date_to,
        doctor_membership_id=resolve_scope_doctor(scope, doctor_membership_id),
    )
    return ServicesReportResponse.from_report(result)
