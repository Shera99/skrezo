"""Какие врачи выполняют услугу (связь услуга ↔ членство).

Отсутствие строк и пустой список — **разные** состояния, и различает их именно
наличие записей:

* строк нет — ограничения нет, услугу делают все. Так живут соло-кабинет и все
  услуги, заведённые до появления этой таблицы: миграция данных не нужна;
* строки есть — услугу делают только они. Наружу отдаются только те, кто ещё
  работает в клинике, поэтому «все назначенные уволены» выглядит как пустой
  список: ограничение осталось, а записывать не к кому.

Уволенного из связи **не вычищаем**: увольнение мягкое (``membership.active``),
и удаление строки потеряло бы настройку прайса — вернувшемуся врачу пришлось бы
проставлять услуги заново. Фильтрация по активности — на чтении.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin


class ServiceDoctor(Base, TimestampMixin):
    __tablename__ = "service_doctor"
    __table_args__ = (
        # Обратный поиск «что умеет этот врач» — для слотов по услуге и проверки
        # при записи; прямой (по услуге) закрыт первичным ключом.
        Index("ix_service_doctor_membership", "membership_id"),
    )

    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("service.id", ondelete="CASCADE"), primary_key=True
    )
    membership_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("membership.id", ondelete="CASCADE"), primary_key=True
    )
