"""Таблица настроек уведомлений на членство (аккаунт + клиника).

Строка — исключение, а не правило: её отсутствие для членства уже значит
дефолт (ничего не отключено, тихих часов нет), поэтому пуши работают и без
единого обращения сюда. Группу ``subscription.*`` эти настройки не касаются:
её нельзя отключить, проверка — на уровне dispatch, а не здесь.
"""

from __future__ import annotations

import uuid
from datetime import time

from sqlalchemy import ForeignKey, Text, Time, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class NotificationPref(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "notification_pref"
    __table_args__ = (
        # Одна строка на членство; заодно и индекс для поиска по (account, clinic).
        UniqueConstraint("account_id", "clinic_id", name="uq_notification_pref_membership"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    # Список отключённых `type` из каталога пушей (app/modules/push/domain/catalog.py).
    disabled: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    # Тихие часы по времени КЛИНИКИ (не устройства); копятся только digest-пуши,
    # срочные (`appointment.*`, `subscription.expired`) идут всегда.
    quiet_from: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_to: Mapped[time | None] = mapped_column(Time, nullable=True)
