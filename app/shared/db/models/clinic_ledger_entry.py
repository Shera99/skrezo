"""Строка журнала операций клиники — только добавление.

``kind``: credit (приход) | debit (расход); ``source`` — откуда деньги или
куда ушли: freedompay_payment, manual_topup, subscription_charge, refund,
adjustment (позже — usage_charge). ``balance_after_tiyin`` — остаток после
операции; ``idempotency_key`` не даёт повторному колбэку провести деньги
дважды; ``provider_payload`` — сырой ответ сервиса как есть.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, UUIDPKMixin


class ClinicLedgerEntry(Base, UUIDPKMixin):
    __tablename__ = "clinic_ledger_entry"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_clinic_ledger_entry_idempotency"),
        Index("ix_clinic_ledger_entry_clinic", "clinic_id", "created_at"),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinic.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    amount_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_tiyin: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="KGS")
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("platform_payment.id", ondelete="SET NULL"), nullable=True
    )
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    provider_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
