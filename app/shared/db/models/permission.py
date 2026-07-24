"""Таблица прав (системный каталог RBAC)."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class Permission(Base, UUIDPKMixin):
    __tablename__ = "permission"

    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    group_key: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Доступно ли право на триале (настраивается из админки). Дефолт — доступно.
    in_trial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
