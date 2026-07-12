"""Таблица аудита — неизменяемый лог мутаций платформенной зоны.

Кто (actor_id → account, платформенный админ Skrezo), что (action), над чем
(entity_type/entity_id), детали (payload jsonb), когда (created_at).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class AuditLog(Base, UUIDPKMixin):
    __tablename__ = "audit_log"

    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
