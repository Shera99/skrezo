"""Таблица заявок клиник (второй путь регистрации — «клиника с сотрудниками»).

Заявку оставляет человек, подтвердивший телефон по OTP (шаг 2 входа), поэтому
``phone`` здесь всегда верифицирован. Клиника по заявке **не создаётся
автоматически**: команда Skrezo рассматривает её в админке и заводит клиента
вручную (``POST /admin/clients``).

Не путать с ``lead`` — та таблица про публичную форму «Больше одного» (без
подтверждения телефона и без города).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import (
    ClinicApplicationStatus,
    ClinicApplicationStatusType,
    DoctorsCountRange,
    DoctorsCountRangeType,
)


class ClinicApplication(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "clinic_application"
    __table_args__ = (
        # Список в админке: фильтр по статусу, свежие сверху.
        Index("ix_clinic_application_status_created", "status", "created_at"),
    )

    company_name: Mapped[str] = mapped_column(Text, nullable=False)
    city_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("city.id"), nullable=False)
    doctors_count: Mapped[DoctorsCountRange] = mapped_column(
        DoctorsCountRangeType, nullable=False
    )
    # Телефон подтверждён OTP; НЕ уникален — одна компания может подать повторно.
    phone: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[ClinicApplicationStatus] = mapped_column(
        ClinicApplicationStatusType,
        nullable=False,
        default=ClinicApplicationStatus.PENDING,
    )
    # Кто из команды Skrezo сменил статус и когда (аудит решения по заявке).
    reviewed_by_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
