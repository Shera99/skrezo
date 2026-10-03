"""Контакты клиники из приложения WhatsApp Business (режим «номер в приложении»).

После подключения номера через Embedded Signup Meta по запросу присылает
адресную книгу приложения и дальше — каждую её правку (вебхук
``smb_app_state_sync``). Имя из адресной книги показывается в списке диалогов,
пока номер не связан с карточкой пациента: так ресепшен узнаёт собеседника,
как в своём телефоне.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class WhatsAppContact(Base, UUIDPKMixin):
    __tablename__ = "whatsapp_contact"
    __table_args__ = (UniqueConstraint("clinic_id", "wa_phone", name="uq_whatsapp_contact_phone"),)

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    #: Номер в E.164 — тот же формат, что ``ai_conversation.wa_phone``.
    wa_phone: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
