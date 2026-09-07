"""Дневные счётчики использования по клинике — основа учёта и тарифных пакетов.

Строка на клинику в день, счётчики только растут (UPSERT-инкремент). Месяц
собирается суммой дней. Токены и вызовы модели — от ассистента и резюме
диалогов; диалог с ассистентом считается один раз за всю жизнь (отметка
``ai_conversation.assistant_counted_at``); исходящие WhatsApp — по категориям
платности из статусов Меты. Сумма в сомах здесь не хранится: её даёт
прайс-лист из настроек, и она пересчитывается, когда меняются ставки.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin

#: Имена счётчиков — они же ключи инкрементов в репозитории.
USAGE_COUNTERS: tuple[str, ...] = (
    "model_calls",
    "model_input_tokens",
    "model_output_tokens",
    "model_cached_tokens",
    "assistant_replies",
    "dialogs",
    "wa_sent",
    "wa_utility",
    "wa_marketing",
    "wa_authentication",
    "wa_billable_other",
    "wa_service_free",
)


class ClinicUsageDay(Base, UUIDPKMixin):
    __tablename__ = "clinic_usage_day"
    __table_args__ = (
        UniqueConstraint("clinic_id", "day", name="uq_clinic_usage_day"),
        Index("ix_clinic_usage_day_day", "day"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    #: Календарный день по UTC — гранулярность учёта, не биллинга.
    day: Mapped[date] = mapped_column(Date, nullable=False)
    model_calls: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    model_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    model_output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    #: Из токенов запроса взято из кэша префикса (Ф8) — тарифицируется дешевле.
    model_cached_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    assistant_replies: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    dialogs: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    wa_sent: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    wa_utility: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    wa_marketing: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    wa_authentication: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    wa_billable_other: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    wa_service_free: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
