"""Роут: GET /clinic/schedule/working-hours — часы работы клиники (settings.view)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.schedule.application.use_cases.get_working_hours import GetWorkingHoursUseCase
from app.modules.schedule.presentation.deps import get_get_working_hours_use_case
from app.modules.schedule.presentation.schemas import WorkingHoursItem, WorkingHoursResponse
from app.shared.auth import ClientContext

router = APIRouter()


@router.get(
    "/clinic/schedule/working-hours",
    response_model=WorkingHoursResponse,
    response_description="Ровно 7 дней недели (0=пн … 6=вс)",
    summary="Часы работы клиники",
)
async def get_working_hours(
    context: ClientContext = Depends(require_permission("settings.view")),
    use_case: GetWorkingHoursUseCase = Depends(get_get_working_hours_use_case),
) -> WorkingHoursResponse:
    """Часы работы и общий обед по дням недели.

    Всегда возвращаются все 7 дней: дни, которые клиника ещё не настраивала,
    отдаются значениями по умолчанию (пн–сб 09:00–18:00, обед 12:00–13:00,
    воскресенье — выходной).
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    days = await use_case.execute(context.clinic_id)
    return WorkingHoursResponse(days=[WorkingHoursItem.from_hours(d) for d in days])
