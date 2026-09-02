"""Таблица эскалаций ассистента на человека (§3.6, §8 ТЗ).

Карточка для администратора: сообщение пациента, что бот собирался ответить
и почему остановился. Пока эскалация не разобрана, диалог в ``escalated`` и
бот молчит — иначе человек и бот ответят одновременно с одного номера.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class AiEscalation(Base, UUIDPKMixin):
    __tablename__ = "ai_escalation"
    __table_args__ = (
        # Лента эскалаций клиники: неразобранные сверху.
        Index("ix_ai_escalation_clinic_open", "clinic_id", "resolved_at", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_conversation.id", ondelete="CASCADE"), nullable=False
    )
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)  # словарь §8.1
    # Предложенный ответ, коды валидатора, сообщение пациента.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
