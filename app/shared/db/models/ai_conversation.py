"""Таблица диалогов WhatsApp с пациентами (канал Cloud API).

Диалог живёт в рамках клиники и номера пациента; ``patient_id`` — привязка к
глобальному пациенту платформы, если номер опознан (иначе NULL до Ф2, где
ассистент заведёт карточку сам).

Активный диалог на пару (клиника, номер) — ровно один: это partial unique в
миграции. Закрывается диалог фоновой задачей после суток тишины; следующий
входящий открывает новый.

``last_patient_msg_at`` — отдельная колонка не случайно: 24-часовое окно Meta
считается от последнего сообщения ПАЦИЕНТА, а ``last_msg_at`` двигают и наши
ответы. Считать окно по общему полю значит продлевать его собственными
сообщениями — и ловить отказ Graph API на «открытом» окне.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import (
    AiConversationStatus,
    AiConversationStatusType,
    Lang,
    LangType,
)


class AiConversation(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "ai_conversation"
    __table_args__ = (
        # Один активный диалог на номер в пределах клиники.
        Index(
            "uq_ai_conversation_active",
            "clinic_id",
            "wa_phone",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # Список диалогов клиники — свежие сверху.
        Index("ix_ai_conversation_clinic_last", "clinic_id", "last_msg_at"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    # Пациент глобальный; NULL — номер пока не опознан.
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("patient.id", ondelete="SET NULL"), nullable=True
    )
    wa_phone: Mapped[str] = mapped_column(Text, nullable=False)  # E.164, +996...
    # Язык диалога: определит Ф2 по первому сообщению; до неё — русский.
    lang: Mapped[Lang] = mapped_column(LangType, nullable=False, server_default=Lang.RU.value)
    status: Mapped[AiConversationStatus] = mapped_column(
        AiConversationStatusType, nullable=False, default=AiConversationStatus.ACTIVE
    )
    last_msg_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_patient_msg_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
