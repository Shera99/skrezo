"""Таблица клиник (тенант). Расширена полями admin-спеки: type/city_id/seats_limit."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import (
    DEFAULT_LANG,
    AiMode,
    AiModeType,
    ClinicType,
    ClinicTypeType,
    Lang,
    LangType,
)

# Таймзона по умолчанию: почти все клиники — в Кыргызстане.
DEFAULT_TIMEZONE = "Asia/Bishkek"


class Clinic(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "clinic"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[ClinicType] = mapped_column(ClinicTypeType, nullable=False)
    # Для соло город заполняется врачом в онбординге → допускаем NULL при создании.
    city_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("city.id"), nullable=True)
    # Для соло — опциональны, видны только в платформенной зоне.
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    lang: Mapped[Lang] = mapped_column(LangType, nullable=False, server_default=DEFAULT_LANG.value)
    # IANA-таймзона клиники: в ней считаются границы дня и рисуется расписание.
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default=DEFAULT_TIMEZONE)
    # Лимит оплаченных мест (врачей) по тарифу — только для type=clinic.
    seats_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Режим AI-ассистента (§13.1): новая клиника — только shadow/off; auto —
    # решение по цифрам теневого режима, включает команда Skrezo.
    ai_mode: Mapped[AiMode] = mapped_column(
        AiModeType, nullable=False, server_default=AiMode.OFF.value
    )
    # Ф7: память смены режима — когда, почему (manual | health | admin) и какая
    # ступень была до выключения, чтобы «включить обратно» вернуло её.
    ai_mode_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ai_mode_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_mode_before_off: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ф7: потолки умений {booking, changes}: off | assisted | auto; пусто — как ступень.
    ai_skills: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Заметки саппорта — видны и правятся только в платформенной зоне.
    support_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
