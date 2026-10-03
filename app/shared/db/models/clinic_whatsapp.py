"""Таблица подключения номера WhatsApp к клинике (Meta Cloud API).

Вебхук Meta не знает наших клиник — он присылает ``phone_number_id`` номера,
на который написал пациент. Эта таблица и есть словарь «номер → клиника»:
без строки здесь входящее сообщение некуда положить, и оно игнорируется.

Токен — два пути подключения. Номер, привязанный командой Skrezo вручную,
работает общим токеном платформы из окружения (``wa_cloud_*``). Номер, который
клиника подключила сама через Embedded Signup, работает СВОИМ бизнес-токеном:
он лежит здесь только зашифрованным (``access_token_enc``, ключ
``DATA_ENCRYPTION_KEY``) и наружу не отдаётся никогда.

Один номер — одна клиника (оба уникальны): Meta не разрешает делить номер
между приложениями, а клиника в MVP подключает один номер.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import WhatsAppMode, WhatsAppModeType


class ClinicWhatsApp(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "clinic_whatsapp"
    __table_args__ = (
        # Вебхук account_update приходит с WABA, а не с номером: поиск по нему.
        Index("ix_clinic_whatsapp_waba", "waba_id"),
    )

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

    # ── Самостоятельное подключение (Embedded Signup) ───────────────────────
    #: manual — привязала команда Skrezo; embedded_signup — клиника из кабинета.
    connected_via: Mapped[str] = mapped_column(
        Text, nullable=False, default="manual", server_default="manual"
    )
    #: Бизнес-токен клиники (Fernet). NULL — номер работает общим токеном Skrezo.
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: PIN двухэтапной проверки, с которым зарегистрирован новый номер (Fernet).
    two_step_pin_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    connected_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Состояние номера по последнему опросу Graph API — для экрана подключения.
    verified_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    quality_rating: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Номер перестал работать у нас (доступ отозван, номер ушёл из Cloud API):
    #: когда и почему; команда Skrezo узнаёт об этом один раз (``notified``).
    needs_reconnect_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    needs_reconnect_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reconnect_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Синхронизация данных приложения WhatsApp Business — по разу в первые сутки.
    contacts_sync_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    history_sync_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Ход импорта истории по данным Meta (0–100) и момент, когда он дошёл до 100.
    history_progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    history_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Meta не отдала историю (клиника отказалась делиться в приложении и т.п.).
    history_error: Mapped[str | None] = mapped_column(Text, nullable=True)
