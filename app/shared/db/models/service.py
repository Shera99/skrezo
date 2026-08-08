"""Таблица услуг клиники (прайс). У каждой клиники свой список услуг."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import ToothStatus, ToothStatusType


class Service(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "service"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_tiyin: Mapped[int] = mapped_column(Integer, nullable=False)  # тыйыны
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # минуты
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # В какое состояние услуга приводит зуб: «Пломба» → filling, «Коронка» → crown,
    # «Удаление» → missing. По нему одонтограмма обновляется сама при завершении
    # приёма — иначе вылеченный зуб остаётся в карте кариозным, пока кто-то не
    # поправит руками, и карта расходится с историей.
    #
    # NULL — «услуга состояние зуба не меняет» (чистка, консультация, снимок) и
    # он же дефолт: выводить исход из НАЗВАНИЯ услуги нельзя. Прайс ведёт клиника
    # («Реставрация II класса», «Лечение кариеса»), и угадывание по строке —
    # ровно та ошибка, из-за которой роль «Ресепшн» когда-то получила доступ к кассе.
    tooth_outcome: Mapped[ToothStatus | None] = mapped_column(ToothStatusType, nullable=True)
