"""Таблица заявок клиник — единая очередь с двумя «дверями».

* **Через OTP** (``POST /clinic-applications``): телефон подтверждён кодом
  (``phone_verified = true``), заявка полная — название, город и размер клиники.
* **С лендинга** (``POST /leads``): код не просили, поэтому телефон **не
  подтверждён** (``phone_verified = false``), а известны только имя и номер;
  название клиники, размер и комментарий — как придут с формы, города нет.

Клиника по заявке **не создаётся автоматически**: команда Skrezo рассматривает её
в админке и заводит клиента вручную (``POST /admin/clients``). Отдельной таблицы
лидов нет — до миграции 0070 она была, но очереди для неё никто не видел.

Что гарантирует схема (а не только код):

* ``ck_clinic_application_verified_complete`` — заявка с подтверждённым телефоном
  всегда полная, поэтому пустые ``company_name``/``city_id``/``doctors_count``
  допустимы только у лида;
* ``ck_clinic_application_lead_named`` — у лида имя обязательно (иначе в очереди
  строка из одного номера);
* ``ux_clinic_application_pending_lead_phone`` — не более одной открытой заявки
  с лендинга на номер. Это замок против гонки двух одновременных отправок формы;
  заявки через OTP по-прежнему могут повторяться.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db.base import Base, TimestampMixin, UUIDPKMixin
from app.shared.db.enums import (
    ClinicApplicationStatus,
    ClinicApplicationStatusType,
    DoctorsCountRange,
    DoctorsCountRangeType,
)

# Имя частичного уникального индекса «один открытый лид на номер» (миграция 0070).
# Прикладной слой узнаёт по нему нарушение дедупа среди прочих IntegrityError.
PENDING_LEAD_INDEX_NAME = "ux_clinic_application_pending_lead_phone"


class ClinicApplication(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "clinic_application"
    __table_args__ = (
        # Список в админке: фильтр по статусу, свежие сверху.
        Index("ix_clinic_application_status_created", "status", "created_at"),
        # Дедуп лидов с лендинга: одна открытая неподтверждённая заявка на номер.
        Index(
            PENDING_LEAD_INDEX_NAME,
            "phone",
            unique=True,
            postgresql_where=text("status = 'pending' AND phone_verified = false"),
        ),
        CheckConstraint(
            "phone_verified = false OR (company_name IS NOT NULL "
            "AND city_id IS NOT NULL AND doctors_count IS NOT NULL)",
            name="ck_clinic_application_verified_complete",
        ),
        CheckConstraint(
            "phone_verified = true OR full_name IS NOT NULL",
            name="ck_clinic_application_lead_named",
        ),
    )

    # У лида с лендинга название клиники, город и размер необязательны
    # (форма спрашивает только имя и телефон) — CHECK выше держит полноту
    # заявок с подтверждённым телефоном.
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    city_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("city.id"), nullable=True)
    doctors_count: Mapped[DoctorsCountRange | None] = mapped_column(
        DoctorsCountRangeType, nullable=True
    )
    # Имя контактного лица: у лида обязательно, у заявки из приложения не спрашивается.
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # НЕ уникален: одна компания может подать повторно (уникальность открытых
    # лидов обеспечивает частичный индекс).
    phone: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Телефон подтверждён OTP. Server_default нет: обе двери задают значение явно.
    phone_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Метка трафика (utm_source или referrer — как пришла с формы).
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ClinicApplicationStatus] = mapped_column(
        ClinicApplicationStatusType,
        nullable=False,
        default=ClinicApplicationStatus.PENDING,
    )
    # Клиника, заведённая по этой заявке (конверсия для дашборда).
    converted_clinic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinic.id", ondelete="SET NULL"), nullable=True
    )
    # Кто из команды Skrezo сменил статус и когда (аудит решения по заявке).
    reviewed_by_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("account.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
