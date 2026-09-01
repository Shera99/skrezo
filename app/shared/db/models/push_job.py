"""Таблица отложенных пушей — normal-приоритет с отменой по показу (ТЗ-СУ §2.4).

``normal`` не уходит пушем немедленно: сначала 10 секунд форы — если адресат
уже смотрит в экран и приложение подтвердило ПОКАЗ записи (``displayed`` с
переднего плана), пуш не нужен и job отменяется. Держать это в памяти процесса
нельзя: рестарт воркера между постановкой и отправкой потерял бы уведомление —
поэтому таблица, а не asyncio-таймер.

Гонку «скан и подтверждение одновременно» решает атомарный захват: строка
уходит в отправку только через ``UPDATE … WHERE sent_at IS NULL AND
canceled_at IS NULL`` — кто первый, тот и прав.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


class PushJob(Base):
    __tablename__ = "push_job"
    __table_args__ = (
        # Скан «что пора отправить»: только живые строки, по сроку.
        Index(
            "ix_push_job_due",
            "send_after",
            postgresql_where=text("sent_at IS NULL AND canceled_at IS NULL"),
        ),
        # Отмена по показу ищет job по записи инбокса.
        Index("ix_push_job_inbox_item", "inbox_item_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    inbox_item_id: Mapped[int] = mapped_column(
        ForeignKey("inbox_item.id", ondelete="CASCADE"), nullable=False
    )
    send_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
