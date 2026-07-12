"""Таблица ролей клиники (RBAC, пер-клиника).

``clinic_id IS NULL`` — глобальный шаблон, клонируется в роли клиники при её
создании. Локализованные названия системных ролей берутся из каталога
сообщений по ``key`` (для кастомных ролей ``name`` задаёт владелец).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class Role(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "role"

    clinic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=True
    )
    key: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
