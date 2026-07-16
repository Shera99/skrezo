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
_PAGE_SIZE = 25


@router.get(
    "/clinic/patients",
    response_model=PatientsResponse,
    summary="Пациенты клиники (у кого есть приём)",
)
async def list_patients(
    query: str | None = Query(default=None, description="Поиск: имя/фамилия/телефон"),
    page: int = Query(default=1, ge=1),
    context: ClientContext = Depends(require_permission("patients.view")),
    use_case: ListPatientsUseCase = Depends(get_list_patients_use_case),
) -> PatientsResponse:
    if context.clinic_id is None:
        raise ForbiddenError("Не выбрана клиника", code="clinic_not_selected")
    result = await use_case.execute(
        context.clinic_id, query=query, page=page, page_size=_PAGE_SIZE
    )
    return PatientsResponse(
        list=[PatientListItem.from_row(r) for r in result.items],
        current_page=result.current_page,
        page_size=result.page_size,
        total_count=result.total_count,
    )
