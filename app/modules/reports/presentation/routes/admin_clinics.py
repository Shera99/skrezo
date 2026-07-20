"""Роут: GET /admin/reports/clinics — свод по всем клиникам (только Skrezo)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.reports.application.use_cases.list_clinics import ListClinicsReportUseCase
from app.modules.reports.presentation.deps import (
    PeriodQuery,
    get_list_clinics_report_use_case,
    period_query,
)
from app.modules.reports.presentation.schemas import ClinicsReportResponse

router = APIRouter()


@router.get(
    "/admin/reports/clinics",
    response_model=ClinicsReportResponse,
    response_description="Все клиники с выручкой, долгом, приёмами и пациентами",
    summary="Свод по клиникам (выбор клиники)",
)
async def clinics_report(
    period: PeriodQuery = Depends(period_query),
    _: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListClinicsReportUseCase = Depends(get_list_clinics_report_use_case),
) -> ClinicsReportResponse:
    """Ключевые метрики по каждой клинике платформы — экран выбора клиники.

    Отсюда фронт переходит в отчёты конкретной клиники
    (``/admin/reports/clinics/{clinicId}/...``). Период здесь считается в таймзоне
    платформы: у клиник она может различаться, а свод — один общий срез.
    """
    result = await use_case.execute(
        period=period.period, date_from=period.date_from, date_to=period.date_to
    )
    return ClinicsReportResponse.from_report(result)
