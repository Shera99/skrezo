"""Доменная структура записи аудита (для чтения)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: uuid.UUID
    actor_id: uuid.UUID
    actor_login: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    payload: dict[str, Any] | None
    created_at: datetime
