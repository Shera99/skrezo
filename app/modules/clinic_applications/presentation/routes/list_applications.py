"""Роут: GET /admin/clinic-applications — список заявок (только Skrezo)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.modules.clinic_applications.application.use_cases.list_applications import (
    ListClinicApplicationsUseCase,
)
from app.modules.clinic_applications.presentation.deps import get_list_applications_use_case
from app.modules.clinic_applications.presentation.schemas import ApplicationsResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.db.enums import ClinicApplicationStatus

router = APIRouter()


@router.get(
    "/admin/clinic-applications",
    response_model=ApplicationsResponse,
    response_description="Заявки (свежие сверху) с пагинацией и счётчиками по статусам",
    summary="Заявки клиник",
)
async def list_applications(
    status: ClinicApplicationStatus | None = Query(
        default=None, description="Фильтр: pending | reviewed | rejected"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListClinicApplicationsUseCase = Depends(get_list_applications_use_case),
) -> ApplicationsResponse:
    """Заявки на подключение клиник: название, город, размер, телефон, статус.

    ``statusCounts`` отдаёт число заявок по каждому статусу — для бейджей в меню.
    """
    result = await use_case.execute(status=status, page=page, page_size=page_size)
    return ApplicationsResponse.from_result(result)
