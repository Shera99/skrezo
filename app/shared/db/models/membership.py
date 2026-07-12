"""Таблица членств: связка «аккаунт ↔ клиника» с ролью (RBAC)."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, true
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin


class Membership(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "membership"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("account.id", ondelete="CASCADE"), nullable=False
    )
    # Роль внутри клиники. Ровно один владелец на клинику — гарантирует
    # прикладной слой (смена владельца в v1 не предусмотрена).
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id"), nullable=False)
    # Специализация — маркер practitioner (кто ведёт приём); NULL для немедицинских ролей.
    specialization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("specialization.id"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
