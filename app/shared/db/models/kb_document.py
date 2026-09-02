"""Таблица базы знаний клиники (§3.4 ТЗ ассистента, spec_1 — без векторов).

Только редко меняющийся текст: подготовка к приёму, что после удаления, как
доехать, рассрочка, правила отмены. Цены/расписание/врачи сюда НЕ кладутся —
они живые и берутся инструментами; дублирование цены в базе знаний — прямой
путь к тому, что бот назовёт устаревшую (проверка — при сохранении документа).

Поиска в первой версии нет: активные документы на языке диалога уходят в
кэшируемый префикс промпта целиком (порог включения поиска — 30 000 токенов
на клинику, отдельной колонкой/таблицей позже; схема миграции не потребует).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class KbDocument(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "kb_document"
    __table_args__ = (
        Index("ix_kb_document_clinic_lang", "clinic_id", "lang"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # Словарь спеки: logistics | aftercare | faq | policy | payment.
    category: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str] = mapped_column(Text, nullable=False)  # ru | ky
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
