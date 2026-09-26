"""Роут: GET /clinic/patients — пациенты клиники (patients.view)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.errors import ForbiddenError
from app.modules.access.presentation.deps import require_permission
from app.modules.patients.application.use_cases.list_patients import ListPatientsUseCase
from app.modules.patients.presentation.deps import get_list_patients_use_case
from app.modules.patients.presentation.schemas import PatientListItem, PatientsResponse
from app.shared.auth import ClientContext

router = APIRouter()


@router.get(
    "/clinic/patients",
    response_model=PatientsResponse,
    summary="Пациенты клиники (у кого есть приём)",
    response_description="Страница пациентов с последним визитом и долгом в этой клинике",
)
async def list_patients(
    query: str | None = Query(default=None, description="Поиск: имя/фамилия/телефон"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=25, ge=1, le=100, alias="pageSize", description="Размер страницы, 1–100"
    ),
    context: ClientContext = Depends(require_permission("patients.view")),
    use_case: ListPatientsUseCase = Depends(get_list_patients_use_case),
) -> PatientsResponse:
    """Пациенты, у которых есть приём в этой клинике, — свежие карточки сверху.

    У каждого — `lastVisitAt` (последний завершённый приём) и `debtTiyin` (долг по
    правилам кассы) именно в этой клинике: пациент глобальный, а визиты и деньги у
    каждой клиники свои. Долг виден всем с правом `patients.view` — врачу тоже
    важно знать, что его пациент должен.
    """
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    result = await use_case.execute(context.clinic_id, query=query, page=page, page_size=page_size)
    return PatientsResponse(
        list=[PatientListItem.from_row(r) for r in result.items],
        current_page=result.current_page,
        page_size=result.page_size,
        total_count=result.total_count,
    )
