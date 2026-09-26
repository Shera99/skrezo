"""Роут: GET /clinic/appointments — приёмы клиники (patients.view_medical)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.appointments.application.use_cases.list_appointments import ListAppointmentsUseCase
from app.modules.appointments.presentation.deps import get_list_appointments_use_case
from app.modules.appointments.presentation.schemas import (
    AppointmentListItem,
    AppointmentsResponse,
)
from app.shared.auth import ClientContext
from app.shared.db.enums import AppointmentStatus

router = APIRouter()
_PAGE_SIZE = 25

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {
        "description": "Граница периода — ни дата `YYYY-MM-DD`, ни дата-время ISO",
        "content": {
            "application/json": {
                "example": {
                    "error": "invalid_period",
                    "message": (
                        "Не разобрать границу периода «30.09.2026»: нужна дата YYYY-MM-DD "
                        "или дата-время ISO"
                    ),
                }
            }
        },
    },
}


@router.get(
    "/clinic/appointments",
    response_model=AppointmentsResponse,
    summary="Приёмы клиники (фильтры + пагинация)",
    response_description="Страница приёмов, свежие сверху; черновики без даты — в конце",
    responses=_ERRORS,
)
async def list_appointments(
    patient_id: uuid.UUID | None = Query(default=None, alias="patientId"),
    appt_status: AppointmentStatus | None = Query(default=None, alias="status"),
    date_from: str | None = Query(
        default=None,
        alias="from",
        description=(
            "С какого момента: дата `YYYY-MM-DD` — с начала дня в поясе клиники, "
            "или дата-время ISO (без пояса — пояс клиники)"
        ),
        examples=["2026-09-01"],
    ),
    date_to: str | None = Query(
        default=None,
        alias="to",
        description=(
            "По какой момент включительно: дата `YYYY-MM-DD` — день целиком в поясе "
            "клиники, или дата-время ISO (без пояса — пояс клиники)"
        ),
        examples=["2026-09-30"],
    ),
    page: int = Query(default=1, ge=1),
    context: ClientContext = Depends(require_permission("patients.view_medical")),
    use_case: ListAppointmentsUseCase = Depends(get_list_appointments_use_case),
) -> AppointmentsResponse:
    """Приёмы клиники с фильтрами по пациенту, статусу и периоду.

    `to=2026-09-30` включает весь 30-й день в поясе клиники: дата без времени —
    это день целиком, а не полночь, после которой последний день выпадал. У
    строки есть `doctorMembershipId` и `patientPhone` — врача сопоставляют по id,
    а телефон не нужно дотягивать отдельным запросом.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    result = await use_case.execute(
        context.clinic_id,
        patient_id=patient_id,
        status=appt_status,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=_PAGE_SIZE,
    )
    return AppointmentsResponse(
        list=[AppointmentListItem.from_row(r) for r in result.items],
        current_page=result.current_page,
        page_size=result.page_size,
        total_count=result.total_count,
    )
