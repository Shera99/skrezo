"""Таблица прав (системный каталог RBAC)."""

from __future__ import annotations

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class Permission(Base, UUIDPKMixin):
    __tablename__ = "permission"

    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    group_key: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
