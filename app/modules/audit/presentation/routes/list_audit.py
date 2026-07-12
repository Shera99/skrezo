"""Роут: GET /admin/audit — журнал аудита (фильтры + курсор)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.modules.audit.application.use_cases.list_audit import ListAuditUseCase
from app.modules.audit.presentation.deps import get_list_audit_use_case
from app.modules.audit.presentation.schemas import AuditEntryOut, AuditListResponse
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id

router = APIRouter()


@router.get(
    "/admin/audit",
    response_model=AuditListResponse,
    summary="Журнал аудита платформенной зоны",
    response_description="Записи (свежие сверху) и курсор следующей страницы",
)
async def list_audit(
    actor_id: uuid.UUID | None = Query(default=None, alias="actor"),
    clinic_id: uuid.UUID | None = Query(default=None, alias="clinicId"),
    from_dt: datetime | None = Query(default=None, alias="from"),
    to_dt: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    _actor: uuid.UUID = Depends(get_authenticated_user_id),
    use_case: ListAuditUseCase = Depends(get_list_audit_use_case),
) -> AuditListResponse:
    result = await use_case.execute(
        actor_id=actor_id,
        clinic_id=clinic_id,
        from_dt=from_dt,
        to_dt=to_dt,
        cursor=cursor,
        limit=limit,
    )
    return AuditListResponse(
        items=[AuditEntryOut.from_entry(e) for e in result.items],
        next_cursor=result.next_cursor,
    )
