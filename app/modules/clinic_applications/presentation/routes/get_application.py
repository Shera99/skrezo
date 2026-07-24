"""Роут: GET /admin/clinic-applications/{id} — карточка заявки (только Skrezo)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.clinic_applications.application.use_cases.get_application import (
    GetClinicApplicationUseCase,
)
from app.modules.clinic_applications.presentation.deps import get_get_application_use_case
from app.modules.clinic_applications.presentation.schemas import ApplicationItem
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Заявка не найдена",
        "content": {
            "application/json": {
                "example": {"error": "application_not_found", "message": "Заявка не найдена"}
            }
        },
    },
}


@router.get(
    "/admin/clinic-applications/{application_id}",
    response_model=ApplicationItem,
    response_description="Заявка со всеми данными и историей рассмотрения",
    summary="Карточка заявки",
    responses=_ERRORS,
)
async def get_application(
    application_id: uuid.UUID,
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetClinicApplicationUseCase = Depends(get_get_application_use_case),
) -> ApplicationItem:
    """Данные заявки: компания, город, размер, подтверждённый телефон, статус."""
    row = await use_case.execute(application_id)
    return ApplicationItem.from_row(row)
