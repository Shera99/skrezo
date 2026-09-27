"""Роут: GET /clinic/reports/doctors — отчёт по врачам (view_all → все, view_own → сам)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.reports.application.use_cases.get_doctors import GetDoctorsReportUseCase
from app.modules.reports.presentation.access import ReportsScope, require_reports_read
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_doctors_report_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import DoctorsReportResponse

router = APIRouter()


@router.get(
    "/clinic/reports/doctors",
    response_model=DoctorsReportResponse,
    response_description=(
        "Строка на врача: приёмы, неявки, начислено, оплачено, долг, средний чек, загрузка"
    ),
    summary="Отчёт по врачам",
)
async def doctors_report(
    period: PeriodQuery = Depends(period_query),
    scope: ReportsScope = Depends(require_reports_read),
    use_case: GetDoctorsReportUseCase = Depends(get_doctors_report_use_case),
) -> DoctorsReportResponse:
    """Показатели врачей за период, по убыванию начисленного.

    Загрузка (``workloadPercent``) — доля рабочего времени периода, занятая
    приёмами: минуты приёмов без отменённых и неявок к рабочим минутам по
    графику (часы клиники ∩ график врача − обед − отсутствия). Для
    ``period=all`` знаменателя нет — ``workingMinutes`` и ``workloadPercent``
    равны ``null``. Ряд по дням и услуги одного врача — ``/revenue`` и
    ``/services`` с ``doctorMembershipId``.

    С правом ``reports.view_all`` — все врачи клиники; с ``reports.view_own`` в
    выдаче останется одна строка — сам вызывающий.
    """
    result = await use_case.execute(
        scope.clinic_id,
        period=period.period,
        date_from=period.date_from,
        date_to=period.date_to,
        own_membership_id=scope.own_membership_id,
    )
    return DoctorsReportResponse.from_report(result)
