"""Роут: журнал отметок «напомнили»."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.reminders.application.use_cases.list_reminder_marks import (
    ListReminderMarksUseCase,
)
from app.modules.reminders.domain.models import ReminderKind
from app.modules.reminders.presentation.deps import get_list_reminder_marks_use_case
from app.modules.reminders.presentation.schemas import ReminderMarksResponse
from app.shared.auth import ClientContext

router = APIRouter()


@router.get(
    "/clinic/reminders/marks",
    response_model=ReminderMarksResponse,
    response_description="Отметки (последние сверху) с пагинацией",
    summary="Журнал отметок «напомнили»",
)
async def list_marks(
    patient_id: uuid.UUID | None = Query(default=None, alias="patientId"),
    kind: ReminderKind | None = Query(default=None, description="Фильтр по поводу"),
    date_from: date | None = Query(default=None, alias="from", description="С даты (tz клиники)"),
    date_to: date | None = Query(default=None, alias="to", description="По дату включительно"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    context: ClientContext = Depends(require_permission("reminders.manage")),
    use_case: ListReminderMarksUseCase = Depends(get_list_reminder_marks_use_case),
) -> ReminderMarksResponse:
    """Отметки клиники с фильтрами по пациенту, поводу и периоду.

    Без дат — за всё время. Для экрана «Напоминания» по каждому пациенту
    достаточно `lastReminderAt` в `GET /clinic/patients`; журнал — для истории
    и карточки пациента (`patientId`).
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    result = await use_case.execute(
        context.clinic_id,
        patient_id=patient_id,
        kind=kind,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return ReminderMarksResponse.from_page(result)
