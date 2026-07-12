"""Роут: PUT /admin/clients/{clinic_id} — частичная правка карточки."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clients.application.use_cases.update_client import UpdateClientUseCase
from app.modules.clients.presentation.deps import get_update_client_use_case
from app.modules.clients.presentation.schemas import ClientDetailResponse, UpdateClientRequest
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


@router.patch(
    "/admin/clients/{clinic_id}",
    response_model=ClientDetailResponse,
    summary="Правка карточки клиента",
    response_description="Обновлённая карточка",
    responses=_NOT_FOUND,
)
async def update_client(
    clinic_id: uuid.UUID,
    body: UpdateClientRequest,
    lang: Lang = Depends(get_request_lang),
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: UpdateClientUseCase = Depends(get_update_client_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> ClientDetailResponse:
    fields = body.model_dump(exclude_unset=True)
    detail = await use_case.execute(clinic_id, fields, lang=lang)
    await audit.record(
        actor_id=actor,
        action="client.updated",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"fields": sorted(fields.keys())},
    )
    return ClientDetailResponse.from_detail(detail)
