"""Таблица специализаций (справочник). Название — в ``specialization_translation``."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, true
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class Specialization(Base, UUIDPKMixin):
    __tablename__ = "specialization"

    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
