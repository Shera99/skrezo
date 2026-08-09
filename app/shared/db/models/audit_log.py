"""Таблица аудита — неизменяемый лог мутаций.

Кто (actor_id → account), что (action), над чем (entity_type/entity_id), детали
(payload jsonb), когда (created_at).

Один лог на два контура, но читаются они порознь:

* **платформенный** (действия Skrezo) — ``clinic_id`` пуст, принадлежность к
  клиенту выражена через ``entity_type='clinic'`` + ``entity_id``;
* **клиничный** (действия сотрудников) — ``clinic_id`` заполнен всегда, потому
  что сущность там своя: приём, платёж, пациент. Именно по нему журнал клиники
  и отбирается, и по нему же держится изоляция между клиниками.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class AuditLog(Base, UUIDPKMixin):
    __tablename__ = "audit_log"

    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False)
    #: Клиника, в контексте которой действовал сотрудник; ``None`` — действие Skrezo.
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # Журнал клиники всегда читают по одной клинике и с конца.
        Index("ix_audit_log_clinic_created", "clinic_id", created_at.desc()),
    )
