"""Кошелёк клиники у Skrezo: остаток и автопродление.

Остаток денормализован из журнала (``clinic_ledger_entry``) и меняется
только вместе с новой строкой журнала под блокировкой этой строки.
``recurring_profile_id`` — задел под автосписание с карты у провайдера:
когда баланса не хватит, спишем с карты, затем — с баланса.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base


class ClinicWallet(Base):
    __tablename__ = "clinic_wallet"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), primary_key=True
    )
    balance_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    autopay_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    #: До какого конца периода владельца уже предупредили о нехватке баланса.
    autopay_warned_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recurring_profile_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
