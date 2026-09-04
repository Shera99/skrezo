"""Таблица сообщений диалогов WhatsApp.

``wa_message_id`` уникален — это и есть дедупликация вебхука: Meta повторяет
доставку при любой заминке, и без уникального индекса пациент получает два
ответа на одно сообщение. Вставка входящих идёт через ``ON CONFLICT DO
NOTHING`` — повтор безвреден.

Ключ — bigint, а не uuid: сообщений на порядки больше остальных сущностей, а
монотонный id даром даёт курсор «подгрузить старше» без сортировки по времени
(``created_at`` у пачки из одного вебхука совпадает).

Поля ``transcript`` / ``used_facts`` / ``validator`` / ``model`` заводятся
сразу, но заполняет их Ф2 (ассистент) — так не придётся мигрировать таблицу с
живой перепиской.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base
from app.shared.db.enums import AiMessageRole, AiMessageRoleType


class AiMessage(Base):
    __tablename__ = "ai_message"
    __table_args__ = (
        # История диалога листается курсором по id (см. докстринг модуля).
        Index("ix_ai_message_conversation_id", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai_conversation.id", ondelete="CASCADE"), nullable=False
    )
    # Денормализация: изоляция клиник проверяется без join на диалог.
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[AiMessageRole] = mapped_column(AiMessageRoleType, nullable=False)
    # Пустая строка — сообщение без текста (голосовое, снимок без подписи).
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    wa_message_id: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    # Медиа: тип и id — словарь Meta (audio/image/document/...), поэтому текст,
    # а не наш enum. ``media_path``/``media_mime`` появляются после скачивания.
    media_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_mime: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ── Заполняет Ф2 (ассистент) ────────────────────────────────────────────
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Уверенность ASR 0..1 (§11): низкая уводит в эскалацию, а не в догадку.
    transcript_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Уверенность модели в ответе (high | medium | low) — метка кабинета
    #: «уверенный ответ» / «проверьте текст».
    confidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Длительность голосового в секундах (Ф5): Meta её не присылает, читаем из файла.
    media_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ── Черновик ассистента в режиме «Проверяю» (Ф5) ─────────────────────────
    #: NULL — не черновик; pending → approved | edited | discarded | superseded.
    review: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Что предлагал бот до правки человеком — материал для обучения (Ф6).
    original_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    used_facts: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB, nullable=True)
    validator: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL — не отправлено (теневой режим или блок валидатора, Ф2). У входящих
    # и доставленных исходящих — момент по данным Meta либо наш.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
