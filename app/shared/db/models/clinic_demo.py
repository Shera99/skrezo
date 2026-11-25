"""Состояние демо-клиники: с какого момента её данные — демонстрационные.

Сброс удаляет только то, что появилось с ``since`` (момент включения признака
``clinic.demo``), и то, что собрал сам: данные клиники до включения он не
трогает никогда. Поэтому включить признак на клинике с историей безопасно.

Собранное сбросом помнится списками, а не датой: записи демо-дня создаются
«задним числом» (иначе правило свежей записи прятало бы их от «не подтвердил»),
и по ``created_at`` их от старых данных клиники не отличить.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


class ClinicDemo(Base):
    __tablename__ = "clinic_demo"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), primary_key=True
    )
    #: Граница сброса; повторное включение признака сдвигает её на «сейчас».
    since: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Когда день собран в последний раз; ``None`` — сброса ещё не было.
    reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Что собрал последний сброс — следующий удалит это независимо от дат.
    appointment_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), nullable=False, server_default=text("'{}'")
    )
    patient_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), nullable=False, server_default=text("'{}'")
    )
