"""Заявка клиники на условия сверх публичных тарифов («нужно больше мест»).

Мест больше, чем в самом большом пакете, кабинет не продаёт: условия
обсуждаются с командой Skrezo, и итог — спецплан (``clinic_custom_plan``).
Заявка — начало этого разговора: что клиника хочет, кто спросил, чем
кончилось. Открытая заявка у клиники одна: повторная отправка обновляет её.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class PlanRequest(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "plan_request"
    __table_args__ = (
        CheckConstraint("seats >= 1", name="ck_plan_request_seats"),
        CheckConstraint("status IN ('new', 'done', 'declined')", name="ck_plan_request_status"),
        # Одна открытая заявка на клинику.
        Index(
            "uq_plan_request_open",
            "clinic_id",
            unique=True,
            postgresql_where=text("status = 'new'"),
        ),
        # Очередь заявок в админке: открытые сверху.
        Index("ix_plan_request_status_created", "status", "created_at"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    #: Сколько мест хочет клиника.
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: new — ждёт Skrezo; done — договорились (обычно поставлен спецплан); declined.
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="new")
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Когда заявку отправили в последний раз (повтор обновляет открытую).
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
