"""Связка «позиция приёма ↔ зуб пациента» — привязка работ к зубам.

Отдельная таблица, а не колонка в позиции, потому что связь **многие-ко-многим**
в обе стороны: одна работа может покрывать несколько зубов (мост на 35–37), а у
позиции зубов может не быть вовсе (чистка, консультация).

Через неё же собирается сквозная история зуба:
``patient_tooth → appointment_item_tooth → appointment_item → appointment``.
Ключ составной — одна позиция не ссылается на один зуб дважды.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


class AppointmentItemTooth(Base):
    __tablename__ = "appointment_item_tooth"
    __table_args__ = (
        # История зуба идёт от зуба к позициям — обратное направление связки.
        Index("ix_appointment_item_tooth_patient_tooth", "patient_tooth_id"),
    )

    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("appointment_item.id", ondelete="CASCADE"), primary_key=True
    )
    patient_tooth_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patient_tooth.id", ondelete="CASCADE"), primary_key=True
    )
