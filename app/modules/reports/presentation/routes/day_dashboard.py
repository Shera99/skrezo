"""Роут: GET /clinic/dashboard — сводка текущего дня клиники."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.reports.application.use_cases.get_day_dashboard import GetDayDashboardUseCase
from app.modules.reports.presentation.access import ReportsScope, require_reports_read
from app.modules.reports.presentation.deps import get_day_dashboard_use_case
from app.modules.reports.presentation.schemas import DayDashboardResponse

router = APIRouter()


@router.get(
    "/clinic/dashboard",
    response_model=DayDashboardResponse,
    response_description="Приёмы на сегодня по статусам, выручка дня и общий долг",
    summary="Сводка текущего дня клиники",
)
async def day_dashboard(
    scope: ReportsScope = Depends(require_reports_read),
    use_case: GetDayDashboardUseCase = Depends(get_day_dashboard_use_case),
) -> DayDashboardResponse:
    """Утренний экран одним запросом.

    Почти то же самое отдаёт `GET /clinic/reports/summary?from=&to=` за сегодня,
    но здесь **сутки определяет сервер** — в таймзоне клиники. Иначе фронту
    пришлось бы сначала узнать её таймзону, а потом просить нужный день, и на
    границе суток он ошибался бы.

    Долг — общий за всё время, а не за сегодня: утром важно, сколько висит
    всего. Права те же, что у отчётов: с ``reports.view_own`` врач видит свой
    день, с ``reports.view_all`` — всю клинику.
    """
    result = await use_case.execute(
        scope.clinic_id, own_membership_id=scope.own_membership_id
    )
    return DayDashboardResponse.from_domain(result)
