"""Use-case: чтение журнала аудита (фильтры + курсор)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.modules.audit.domain.models import AuditEntry
from app.modules.audit.infrastructure.repositories.audit_repository import AuditRepository
from app.shared.api.pagination import decode_cursor, encode_cursor


@dataclass(slots=True)
class ListAuditResult:
    items: list[AuditEntry]
    next_cursor: str | None


class ListAuditUseCase:
    def __init__(self, audit: AuditRepository) -> None:
        self._audit = audit

    async def execute(
        self,
        *,
        actor_id: uuid.UUID | None,
        clinic_id: uuid.UUID | None = None,
        clinic_scope: uuid.UUID | None = None,
        platform_only: bool = False,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ListAuditResult:
        decoded = decode_cursor(cursor) if cursor else None
        items, next_tuple = await self._audit.list(
            actor_id=actor_id,
            clinic_id=clinic_id,
            clinic_scope=clinic_scope,
            platform_only=platform_only,
            from_dt=from_dt,
            to_dt=to_dt,
            cursor=decoded,
            limit=limit,
        )
        next_cursor = encode_cursor(next_tuple[0], next_tuple[1]) if next_tuple else None
        return ListAuditResult(items, next_cursor)
