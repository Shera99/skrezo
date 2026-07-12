"""Таблица лидов (заявка «Больше одного»).

Дедупликация по телефону (один номер — одна заявка). Обработана заявка или нет
— видно по ``converted_clinic_id`` (панель заявок со статусами — v2).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class Lead(Base, UUIDPKMixin):
    __tablename__ = "lead"

    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    clinic_name: Mapped[str] = mapped_column(Text, nullable=False)
    doctors_count_range: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    converted_clinic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinic.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
