"""Роут: POST /admin/clients/{id}/archive — архивация клиента."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from app.modules.audit.application.services.audit_service import AuditService
from app.modules.audit.presentation.deps import get_audit_service
from app.modules.clients.application.use_cases.archive_client import ArchiveClientUseCase
from app.modules.clients.presentation.deps import get_archive_client_use_case
from app.modules.clients.presentation.schemas import ArchiveClientRequest
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.shared.api.schemas import OkResponse

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {404: {"description": "Клиент не найден"}}


@router.post(
    "/admin/clients/{clinic_id}/archive",
    response_model=OkResponse,
    summary="Архивация клиента",
    response_description="Подписка → cancelled, доступ закрыт, данные хранятся",
    responses=_ERRORS,
)
async def archive_client(
    clinic_id: uuid.UUID,
    body: ArchiveClientRequest,
    actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ArchiveClientUseCase = Depends(get_archive_client_use_case),
    audit: AuditService = Depends(get_audit_service),
) -> OkResponse:
    await use_case.execute(clinic_id, body.reason)
    await audit.record(
        actor_id=actor,
        action="client.archived",
        entity_type="clinic",
        entity_id=clinic_id,
        payload={"reason": body.reason} if body.reason else None,
    )
    return OkResponse()
