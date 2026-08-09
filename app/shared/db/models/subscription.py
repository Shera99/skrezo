"""Таблица подписки клиники на Skrezo (одна активная запись на клинику).

История статусов — в ``subscription_event``; сама запись хранит текущее
состояние.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import SubscriptionStatus, SubscriptionStatusType


class Subscription(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "subscription"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[SubscriptionStatus] = mapped_column(SubscriptionStatusType, nullable=False)
    trial_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Гейт карты отключён (1 месяц бесплатно всем, без карты) → NULL.
    # Поле оставлено на будущее, если вернётся требование карты.
    card_attach_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    card_attached_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Когда владельцу отправили «триал скоро кончится». Отметка нужна для
    # идемпотентности: джоба ходит ежедневно, а предупреждение уместно один раз.
    # Хранится здесь, а не в subscription_event: там лог переходов СТАТУСА, а
    # предупреждение статус не меняет.
    trial_warning_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
