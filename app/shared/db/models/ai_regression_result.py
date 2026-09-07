"""Итог одного кейса в одном прогоне (Ф6)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class AiRegressionResult(Base, UUIDPKMixin):
    __tablename__ = "ai_regression_result"
    __table_args__ = (UniqueConstraint("run_id", "case_id", name="uq_ai_regression_result_case"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_regression_run.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_regression_case.id", ondelete="CASCADE"), nullable=False
    )
    #: passed | failed | skipped
    status: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[str] = mapped_column(Text, nullable=False)
    actual: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
