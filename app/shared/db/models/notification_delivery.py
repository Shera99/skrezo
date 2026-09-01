"""Таблица телеметрии доставки уведомлений (ТЗ-СУ §2.5).

Одна строка — одна попытка доставить запись инбокса на одно устройство.
Приложение подтверждает ПОКАЗ (``displayed_at``), сервер сравнивает с
отправленным (``sent_at``) — через месяц видна реальная доставляемость по
платформам, а не догадки про энергосберегайки Xiaomi. Ошибка провайдера
(включая мёртвый токен, Д16) остаётся здесь же — в ``error``.

Хранение — 3 месяца (§14), чистит ночная задача ``cleanup_notifications``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


class NotificationDelivery(Base):
    __tablename__ = "notification_delivery"
    __table_args__ = (
        # Подтверждение показа находит строки по записи инбокса.
        Index("ix_notification_delivery_inbox", "inbox_item_id"),
        # Сводка «за N дней» и уборка по возрасту.
        Index("ix_notification_delivery_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    inbox_item_id: Mapped[int] = mapped_column(
        ForeignKey("inbox_item.id", ondelete="CASCADE"), nullable=False
    )
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    # Канал доставки: пока только ``push``; ``socket`` появится с Ф4.
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str | None] = mapped_column(Text, nullable=True)  # ios | android
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)  # fcm | apns | log
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    displayed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
