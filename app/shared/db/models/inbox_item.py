"""Таблица инбокса — источник истины «что произошло и прочитано ли» (ТЗ-СУ §2.2).

Адресат — **снимок** на момент отправки (``account_id`` + ``clinic_id``), а не
вычисление по текущей роли: у сменившего роль сотрудника история не должна
переписываться задним числом. Сотрудник двух клиник получает два независимых
инбокса — поэтому пара, а не голый аккаунт.

Уникальность ``(event_id, account_id)`` делает повторную обработку события
безвредной: диспетчер идемпотентен. Текст (``title``/``body``) фиксируется на
языке адресата в момент доставки — инбокс потом не перерендеривается.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base
from app.shared.db.enums import EventPriority, EventPriorityType, Lang, LangType


class InboxItem(Base):
    __tablename__ = "inbox_item"
    __table_args__ = (
        UniqueConstraint("event_id", "account_id", name="uq_inbox_item_event_account"),
        # Лента инбокса: свои записи, свежие сверху (id монотонен).
        Index("ix_inbox_item_owner", "account_id", "clinic_id", "id"),
        # Счётчик непрочитанного — самый частый запрос (бейдж).
        Index(
            "ix_inbox_item_unread",
            "account_id",
            "clinic_id",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("event.id", ondelete="CASCADE"), nullable=False
    )
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)  # тип события — иконка/фильтр
    priority: Mapped[EventPriority] = mapped_column(EventPriorityType, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[Lang] = mapped_column(LangType, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
