"""Архив переписки из приложения WhatsApp Business до подключения номера.

При подключении номера с сохранением приложения Meta один раз отдаёт историю
чатов за последние месяцы (вебхук ``history``) — пачками, сначала свежие дни,
потом более старые. В живую ленту ``ai_message`` такие сообщения не кладутся
намеренно: она упорядочена по id вставки, и прошлогоднее сообщение, пришедшее
позже вчерашнего, встало бы в чат ниже него, а ассистент принял бы его за
последнюю реплику пациента. Архив упорядочен по времени отправки и показывается
над живой перепиской отдельной лентой.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


class WhatsAppHistoryMessage(Base):
    __tablename__ = "whatsapp_history_message"
    __table_args__ = (
        # Лента архива диалога листается от свежих к старым.
        Index("ix_whatsapp_history_conversation_sent", "conversation_id", "sent_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_conversation.id", ondelete="CASCADE"), nullable=False
    )
    #: Дедупликация: Meta может прислать пачку повторно.
    wa_message_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    #: in — написал собеседник, out — клиника из приложения.
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    #: Тип по словарю Meta; медиа в истории приходит без файла (media_placeholder).
    media_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Статус из истории: DELIVERED | READ | PLAYED | …
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
