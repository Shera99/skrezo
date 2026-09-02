"""Таблица журнала вызовов инструментов ассистента (§3.7 ТЗ).

Нужен для двух вещей: валидатор сверяет факты ответа с тем, что модель
РЕАЛЬНО получила инструментами в этом ходу, а при споре с клиникой видно,
откуда взялось каждое число.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


class AiToolCall(Base):
    __tablename__ = "ai_tool_call"
    __table_args__ = (Index("ix_ai_tool_call_conversation", "conversation_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_conversation.id", ondelete="CASCADE"), nullable=False
    )
    # Ответ бота, к которому относится ход (проставляется после сохранения).
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("ai_message.id", ondelete="SET NULL"), nullable=True
    )
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
