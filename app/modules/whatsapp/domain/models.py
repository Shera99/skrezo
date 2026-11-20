"""Доменные структуры чтения модуля WhatsApp."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.modules.whatsapp.domain.connection import ConnectionState, connection_state
from app.shared.db.enums import AiConversationStatus, AiMessageRole, Lang, WhatsAppMode


@dataclass(frozen=True, slots=True)
class ConversationRow:
    """Строка списка диалогов: шапка, превью и состояние для вкладок кабинета (Ф5)."""

    id: uuid.UUID
    wa_phone: str
    patient_id: uuid.UUID | None
    patient_name: str | None
    lang: Lang
    status: AiConversationStatus
    last_msg_at: datetime
    last_patient_msg_at: datetime | None
    last_body: str | None
    #: Роль и медиа последнего сообщения — превью «🎙 Голосовое · 0:14».
    last_role: AiMessageRole | None = None
    last_media_type: str | None = None
    last_media_duration_sec: int | None = None
    #: Непрочитанное (сообщения пациента новее общей отметки клиники).
    unread_count: int = 0
    #: Черновик ассистента, ждущий решения («Проверяю»).
    pending_draft_id: int | None = None
    #: Имя из адресной книги приложения WhatsApp Business клиники.
    contact_name: str | None = None

    @property
    def awaiting_reply(self) -> bool:
        """Последнее слово за пациентом, диалог не закрыт — клиника ещё не ответила."""
        return (
            self.last_role is AiMessageRole.PATIENT
            and self.status is not AiConversationStatus.CLOSED
        )

    @property
    def needs_human(self) -> bool:
        """Передан человеку либо ждёт решения по черновику."""
        return self.status is AiConversationStatus.ESCALATED or self.pending_draft_id is not None


@dataclass(frozen=True, slots=True)
class ConversationCounters:
    """Числа на вкладках списка и бейдж раздела."""

    total: int
    awaiting: int
    needs_human: int
    unread: int


@dataclass(frozen=True, slots=True)
class ConnectionRow:
    """Подключение WhatsApp одной клиники — строка списка для команды Skrezo."""

    clinic_id: uuid.UUID
    clinic_name: str
    phone_number_id: str
    display_phone: str
    verified_name: str | None
    waba_id: str | None
    mode: WhatsAppMode
    active: bool
    #: ``manual`` — привязала команда Skrezo; ``embedded_signup`` — клиника сама.
    connected_via: str
    #: Когда подключён; у привязки вручную — когда команда завела строку.
    connected_at: datetime
    quality_rating: str | None
    status_checked_at: datetime | None
    needs_reconnect_at: datetime | None
    needs_reconnect_reason: str | None

    @property
    def state(self) -> ConnectionState:
        """``working`` / ``needs_reconnect`` / ``disconnected``."""
        return connection_state(
            active=self.active, needs_reconnect=self.needs_reconnect_at is not None
        )


@dataclass(frozen=True, slots=True)
class ConnectionSummary:
    """Числа по всем подключениям — для вкладок списка, без учёта фильтров."""

    total: int
    working: int
    needs_reconnect: int
    disconnected: int
    embedded_signup: int
    manual: int
