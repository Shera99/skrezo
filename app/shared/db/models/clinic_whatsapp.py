"""Таблица подключения номера WhatsApp к клинике (Meta Cloud API).

Вебхук Meta не знает наших клиник — он присылает ``phone_number_id`` номера,
на который написал пациент. Эта таблица и есть словарь «номер → клиника»:
без строки здесь входящее сообщение некуда положить, и оно игнорируется.

Токен доступа приложения Meta здесь НЕ хранится — секреты живут в переменных
окружения (``wa_cloud_*``). Таблица держит только идентификаторы номера.

Один номер — одна клиника (оба уникальны): Meta не разрешает делить номер
между приложениями, а клиника в MVP подключает один номер.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import WhatsAppMode, WhatsAppModeType


class ClinicWhatsApp(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "clinic_whatsapp"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    # Идентификатор номера в Meta — по нему вебхук находит клинику.
    phone_number_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Человекочитаемый номер (E.164) — для показа и для распознавания эха
    # собственных исходящих в режиме Coexistence.
    display_phone: Mapped[str] = mapped_column(Text, nullable=False)
    # WhatsApp Business Account — нужен для синхронизации шаблонов.
    waba_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[WhatsAppMode] = mapped_column(
        WhatsAppModeType, nullable=False, default=WhatsAppMode.API_ONLY
    )
    # Выключенная привязка сохраняет историю, но перестаёт принимать вебхук.
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
