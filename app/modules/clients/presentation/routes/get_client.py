"""Роут: GET /admin/clients/{clinic_id} — карточка клиента."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.clients.application.use_cases.get_client import GetClientUseCase
from app.modules.clients.presentation.deps import get_get_client_use_case
from app.modules.clients.presentation.schemas import ClientDetailResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.db.enums import Lang
from app.shared.i18n.lang import get_request_lang

router = APIRouter()

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "Клиент не найден",
        "content": {"application/json": {"example": {"error": "client_not_found"}}},
    },
}


@router.get(
    "/admin/clients/{clinic_id}",
    response_model=ClientDetailResponse,
    summary="Карточка клиента",
    responses=_NOT_FOUND,
)
async def get_client(
    clinic_id: uuid.UUID,
    lang: Lang = Depends(get_request_lang),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: GetClientUseCase = Depends(get_get_client_use_case),
) -> ClientDetailResponse:
    detail = await use_case.execute(clinic_id, lang=lang)
    return ClientDetailResponse.from_detail(detail)
