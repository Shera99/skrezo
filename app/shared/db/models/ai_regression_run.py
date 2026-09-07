"""Прогон регрессии клиники (Ф6): режим, статус, счётчики, версия промпта."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class AiRegressionRun(Base, UUIDPKMixin):
    __tablename__ = "ai_regression_run"
    __table_args__ = (Index("ix_ai_regression_run_clinic", "clinic_id", "created_at"),)

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    #: offline — без модели (медфильтр, валидатор, наличие знания в промпте);
    #: model — сухой прогон модели по каждому кейсу.
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    #: queued | running | done | failed
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    trigger: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    passed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
