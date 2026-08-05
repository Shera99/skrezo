"""Таблица пакетов тарифа клиники (только для версий type=clinic).

Модель «пакеты»: именованный пакет покрывает клинику до ``up_to`` мест за
фиксированную ``price``. Границы ``up_to`` и цены ``price`` строго возрастают.
Сверх последнего пакета — доплата ``tariff_version.over_price`` за место («Сеть»).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class TariffPackage(Base, UUIDPKMixin):
    __tablename__ = "tariff_package"

    tariff_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tariff_version.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    up_to: Mapped[int] = mapped_column(Integer, nullable=False)  # верхняя граница мест (вкл.)
    price_tiyin: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # тыйыны за клинику до up_to мест
