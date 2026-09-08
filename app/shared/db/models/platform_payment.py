"""Платёж клиники за подписку Skrezo через платёжного провайдера.

Статусы: ``pending`` — создан, владелец ушёл на страницу оплаты;
``paid`` — подтверждён серверным колбэком, период открыт; ``failed`` —
провайдер сообщил об отказе; ``refunded`` — возврат отмечен командой Skrezo.
``pg_order_id`` у провайдера = ``id`` этой строки.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class PlatformPayment(Base, UUIDPKMixin):
    __tablename__ = "platform_payment"
    __table_args__ = (
        Index("ix_platform_payment_clinic", "clinic_id", "created_at"),
        Index("ix_platform_payment_status", "status", "created_at"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="freedompay")
    provider_payment_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Профиль автосписания провайдера — когда его дадут.
    recurring_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    amount_tiyin: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="KGS")
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    redirect_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Последний колбэк провайдера как есть (без секретов — их там нет).
    callback: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
