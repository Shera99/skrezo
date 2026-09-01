"""Доменные структуры чтения модуля WhatsApp."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.shared.db.enums import AiConversationStatus, Lang


@dataclass(frozen=True, slots=True)
class ConversationRow:
    """Строка списка диалогов: шапка + превью последнего сообщения."""

    id: uuid.UUID
    wa_phone: str
    patient_id: uuid.UUID | None
    patient_name: str | None
    lang: Lang
    status: AiConversationStatus
    last_msg_at: datetime
    last_patient_msg_at: datetime | None
    last_body: str | None
