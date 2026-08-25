"""Таблица токенов устройств для пуш-уведомлений.

Токен привязан к КОНКРЕТНОМУ членству (аккаунт + клиника): события одной
клиники не должны идти на устройство, пока сотрудник числится в другой —
сменил клинику на том же телефоне, и пуши старой клиники прекращаются. Один
человек может держать несколько устройств одновременно (телефон + планшет).

``token`` уникален на всю платформу: FCM/APNs выдают токен приложению, а не
паре (аккаунт, клиника), поэтому регистрация с новыми ``account_id``/
``clinic_id`` для уже известного токена — это переезд того же устройства
(смена клиники, повторный вход другим сотрудником), а не отдельная запись.

Токен протухает молча: транспорт (FCM/APNs) сообщает об этом через
``NotRegistered``/``Unregistered``, и такой токен удаляется без ретраев —
отдельного поля-статуса «жив ли токен» таблица не хранит.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin
from app.shared.db.enums import (
    DEFAULT_LANG,
    DevicePlatform,
    DevicePlatformType,
    Lang,
    LangType,
)


class Device(Base, UUIDPKMixin):
    __tablename__ = "device"
    __table_args__ = (
        # Уборка устройств уволенного и фан-аут «на все устройства клиники»
        # (sync.day) — самые частые запросы к таблице.
        Index("ix_device_account", "account_id"),
        Index("ix_device_clinic", "clinic_id"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    platform: Mapped[DevicePlatform] = mapped_column(DevicePlatformType, nullable=False)
    app_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Язык текста пуша — с самого устройства, не из настроек клиники.
    locale: Mapped[Lang] = mapped_column(
        LangType, nullable=False, server_default=DEFAULT_LANG.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Обновляется при каждой повторной регистрации того же токена.
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
