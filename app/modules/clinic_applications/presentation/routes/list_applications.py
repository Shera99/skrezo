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
    phone_verified: bool | None = Query(
        default=None,
        alias="phoneVerified",
        description=(
            "Фильтр по двери очереди: `true` — только заявки из приложения (телефон "
            "подтверждён кодом), `false` — только лиды с лендинга. Не задан — все"
        ),
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100, alias="pageSize"),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListClinicApplicationsUseCase = Depends(get_list_applications_use_case),
) -> ApplicationsResponse:
    """Единая очередь заявок: имя, телефон, компания, город, размер, статус.

    В очереди две двери: заявки из приложения (`POST /clinic-applications`,
    `phoneVerified = true`) и лиды с лендинга (`POST /leads`,
    `phoneVerified = false`). У лида название компании, город и размер могут быть
    пустыми, а имя — `fullName` — есть всегда; по неподтверждённому номеру нельзя
    отправлять доступы.

    `statusCounts` отдаёт число заявок по каждому статусу — для бейджей в меню.
    Счётчики учитывают фильтр `phoneVerified` (но не `status`), поэтому чипы
    статусов совпадают с отфильтрованным списком.
    """
    result = await use_case.execute(
        status=status, phone_verified=phone_verified, page=page, page_size=page_size
    )
    return ApplicationsResponse.from_result(result)
