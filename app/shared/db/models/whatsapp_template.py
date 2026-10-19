"""Таблица одобренных шаблонов WhatsApp (для сообщений вне 24-часового окна).

После суток молчания пациента свободный текст запрещён правилами Meta — можно
отправлять только заранее одобренные шаблоны. Здесь — их локальный каталог,
синхронизируемый из кабинета Meta: интерфейсу нужно что показывать вместо поля
ввода, не дёргая Graph API на каждое открытие диалога.

``lang``, ``category`` и ``status`` — словари Meta (в статусах бывают ещё
``PAUSED``/``DISABLED``), поэтому текст, а не наши enum'ы: чужой словарь
меняется без нас, и enum превращал бы каждую новинку Meta в миграцию.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class WhatsAppTemplate(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "whatsapp_template"
    __table_args__ = (
        # Upsert при синхронизации: шаблон определяется именем и языком. Два
        # partial-индекса вместо одного: NULL в clinic_id (общий шаблон) в
        # обычном unique дал бы дубли.
        Index(
            "uq_whatsapp_template_clinic",
            "clinic_id",
            "meta_name",
            "lang",
            unique=True,
            postgresql_where=text("clinic_id IS NOT NULL"),
        ),
        Index(
            "uq_whatsapp_template_common",
            "meta_name",
            "lang",
            unique=True,
            postgresql_where=text("clinic_id IS NULL"),
        ),
    )

    # NULL — общий шаблон платформы (доступен всем клиникам).
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=True
    )
    meta_name: Mapped[str] = mapped_column(Text, nullable=False)  # имя в кабинете Meta
    lang: Mapped[str] = mapped_column(Text, nullable=False)  # код языка Meta (ru, ky_KG…)
    category: Mapped[str] = mapped_column(Text, nullable=False)  # MARKETING/UTILITY/…
    body_preview: Mapped[str] = mapped_column(Text, nullable=False)  # показ администратору
    variables: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    # Момент последней смены статуса при синхронизации — из него складывается
    # причина недоступности «на проверке в Meta с 20 сентября». NULL у строк,
    # сверенных до появления поля: момент неизвестен, дата не показывается.
    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Понятное название и подписи переменных: у общих шаблонов платформы их пишет
    # команда Skrezo в админке, у шаблонов клиники — сама клиника (с 19.10.2026).
    # Без них показывается имя из Meta и сами плейсхолдеры.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    variable_labels: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Откуда брать значение каждой переменной для предзаполнения при отправке
    # (``whatsapp/domain/template_sources.py``); пустой список — все «вручную».
    # Синхронизация с Meta его не трогает, как и подписи.
    variable_sources: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
