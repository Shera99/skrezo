"""Кейс регрессии клиники — выученный ответ или инцидент (Ф6).

Виды (``kind``): ``knowledge`` — бот обязан ответить из базы знаний;
``reply`` — ответ, одобренный человеком; ``refusal`` — инцидент выдумки, бот
обязан отказать или опереться на факты; ``medical`` — медфильтр. Ожидание
(``expect``) зависит от вида, происхождение (``origin``) — ссылки на диалог,
сообщение, пробел, документ.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class AiRegressionCase(Base, UUIDPKMixin):
    __tablename__ = "ai_regression_case"
    __table_args__ = (
        UniqueConstraint("clinic_id", "kind", "normalized", name="uq_ai_regression_case_question"),
        Index("ix_ai_regression_case_clinic_active", "clinic_id", "is_active", "created_at"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str] = mapped_column(Text, nullable=False)  # ru | ky
    question: Mapped[str] = mapped_column(Text, nullable=False)
    normalized: Mapped[str] = mapped_column(Text, nullable=False)
    expect: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: knowledge_gap | approved_draft | edited_draft | incident | medical | manual
    source: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    #: passed | failed | skipped — итог последнего прогона.
    last_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
