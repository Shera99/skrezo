"""Пробел знаний ассистента — вопрос, на который бот не ответил (Ф6).

Одна строка на «смысл вопроса» в клинике: похожие формулировки склеиваются в
неё со счётчиком ``ask_count``. Жизненный цикл: ``unknown`` (в очереди
«Не знает») → ``answered`` (администратор ответил, родился документ базы
знаний) либо ``dismissed`` («не нужно отвечать»).

``source`` — откуда узнали о пробеле: ``low_confidence`` (модель честно
сказала «не знаю»), ``no_facts`` (валидатор снял конкретику без опоры на
факты), ``model_unknown`` (модель позвала человека с причиной unknown).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class AiKnowledgeGap(Base, UUIDPKMixin):
    __tablename__ = "ai_knowledge_gap"
    __table_args__ = (
        Index("ix_ai_knowledge_gap_clinic_status", "clinic_id", "status", "last_asked_at"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    #: Дословно, как спросил пациент в первый раз.
    question: Mapped[str] = mapped_column(Text, nullable=False)
    #: Нормализованная форма для склейки (регистр, пунктуация, ё→е).
    normalized: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str] = mapped_column(Text, nullable=False)  # ru | ky
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="unknown")
    source: Mapped[str] = mapped_column(Text, nullable=False)
    #: Что сама модель сказала о пробеле (human_reason / note) — подсказка админу.
    hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    ask_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    first_asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Последние диалоги, где спрашивали (до пяти) — чтобы открыть контекст.
    conversation_ids: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    kb_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("kb_document.id", ondelete="SET NULL"), nullable=True
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
