"""Таблица событий подписки (лог переходов статуса — для поддержки/аудита)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin
from app.shared.db.enums import SubscriptionStatus, SubscriptionStatusType


class SubscriptionEvent(Base, UUIDPKMixin):
    __tablename__ = "subscription_event"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subscription.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[SubscriptionStatus | None] = mapped_column(
        SubscriptionStatusType, nullable=True
    )
    to_status: Mapped[SubscriptionStatus] = mapped_column(SubscriptionStatusType, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
