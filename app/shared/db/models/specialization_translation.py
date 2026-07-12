"""Переводы названий специализаций (по языку)."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TranslationMixin, UUIDPKMixin


class SpecializationTranslation(Base, UUIDPKMixin, TranslationMixin):
    __tablename__ = "specialization_translation"
    __table_args__ = (
        UniqueConstraint("specialization_id", "lang", name="uq_specialization_translation_lang"),
    )

    specialization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("specialization.id", ondelete="CASCADE"), nullable=False
    )
