"""Схемы журнала аудита."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app.modules.audit.domain.models import AuditEntry
from app.shared.api.schemas import CamelModel


class AuditEntryOut(CamelModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    actor_login: str | None = None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None = None
    payload: dict[str, Any] | None = None
    created_at: datetime

    @classmethod
    def from_entry(cls, entry: AuditEntry) -> AuditEntryOut:
        return cls(
            id=entry.id,
            actor_id=entry.actor_id,
            actor_login=entry.actor_login,
            action=entry.action,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            payload=entry.payload,
            created_at=entry.created_at,
        )


class AuditListResponse(CamelModel):
    items: list[AuditEntryOut]
    next_cursor: str | None = None
