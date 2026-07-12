"""Таблица версий тарифа (версионирование без удаления).

Текущая версия каждого типа = запись с максимальным ``created_at`` (и
``version``). Старые версии не удаляются — нужны как основание для прошлых
платежей. ``version`` — целочисленный номер, растёт по типу от 1.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin
from app.shared.db.enums import ClinicType, ClinicTypeType


class TariffVersion(Base, UUIDPKMixin):
    __tablename__ = "tariff_version"

    type: Mapped[ClinicType] = mapped_column(ClinicTypeType, nullable=False)
    # Целочисленный номер версии (растёт по типу от 1).
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Только для type=solo (фиксированная цена); для clinic — NULL (цена в пакетах).
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Только для type=clinic: цена за место сверх последнего пакета («Сеть», 11+).
    over_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id"), nullable=False)
