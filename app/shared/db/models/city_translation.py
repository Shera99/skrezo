"""Переводы названий городов (по языку)."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TranslationMixin, UUIDPKMixin


class CityTranslation(Base, UUIDPKMixin, TranslationMixin):
    __tablename__ = "city_translation"
    __table_args__ = (UniqueConstraint("city_id", "lang", name="uq_city_translation_lang"),)

    city_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("city.id", ondelete="CASCADE"), nullable=False
    )
