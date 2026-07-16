"""Таблица услуг клиники (прайс). У каждой клиники свой список услуг."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class Service(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "service"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[int] = mapped_column(Integer, nullable=False)  # целые сомы
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # минуты
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
