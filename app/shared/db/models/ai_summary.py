"""Резюме закрытого диалога (§3.3 ТЗ ассистента).

Диалог, молчащий сутки, закрывается фоновой задачей — и от него остаётся
короткая выжимка: зачем человек писал и чем кончилось. В контекст нового
диалога подкладываются последние пять резюме по этому номеру, поэтому
разговор не начинается с нуля («вы записывались на чистку в августе»).

Медицинских подробностей в резюме нет намеренно: пересказ жалоб, прошедший
через модель, — ровно тот материал, которому нельзя доверять, и хранить его
как факт о пациенте мы не будем.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class AiSummary(Base, UUIDPKMixin):
    __tablename__ = "ai_summary"
    __table_args__ = (
        # Подбор в промпт: последние пять по номеру в пределах клиники.
        Index("ix_ai_summary_clinic_phone", "clinic_id", "wa_phone", "created_at"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    # Диалог может быть удалён вместе с историей — резюме переживает его.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ai_conversation.id", ondelete="SET NULL"), nullable=True
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("patient.id", ondelete="SET NULL"), nullable=True
    )
    #: Ключ подбора: номер переживает и смену карточки пациента.
    wa_phone: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
