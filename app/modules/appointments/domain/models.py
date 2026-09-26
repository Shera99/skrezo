"""Доменные структуры чтения модуля приёмов."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.shared.db.enums import AppointmentItemStatus, AppointmentStatus, AppointmentType
from app.shared.db.models.appointment import Appointment


@dataclass(frozen=True, slots=True)
class ItemInput:
    """Позиция чека на входе: услуга, цена за единицу, количество и зубы.

    ``teeth`` пуст у работ, не привязанных к зубу (чистка, консультация), и
    содержит несколько номеров, когда одна работа покрывает несколько зубов.
    ``price_tiyin`` не задан — берётся цена услуги на момент оказания.
    ``status`` отличает запланированное от сделанного и отменённого внутри
    одного приёма.
    """

    service_id: uuid.UUID | None
    price_tiyin: int | None
    quantity: int
    teeth: list[int]
    comment: str | None
    status: AppointmentItemStatus = AppointmentItemStatus.PLANNED


@dataclass(frozen=True, slots=True)
class AppointmentRow:
    id: uuid.UUID
    patient_id: uuid.UUID
    patient_name: str
    doctor_name: str | None
    appointment_type: AppointmentType
    scheduled_at: datetime | None  # None у черновика (status=draft)
    duration_min: int
    status: AppointmentStatus
    price_total_tiyin: int
    #: Членство врача — фронт сопоставляет врача по id, а не по имени.
    doctor_membership_id: uuid.UUID | None = None
    #: Телефон пациента — без отдельного запроса карточки.
    patient_phone: str = ""


@dataclass(frozen=True, slots=True)
class AppointmentItemRow:
    """Позиция приёма для выдачи: цена за единицу, количество и итог строки."""

    id: uuid.UUID
    service_id: uuid.UUID | None
    service_name: str | None
    status: AppointmentItemStatus
    price_tiyin: int
    quantity: int
    teeth: list[int]
    comment: str | None

    @property
    def total_tiyin(self) -> int:
        """Итог строки: цена за единицу × количество."""
        return self.price_tiyin * self.quantity


@dataclass(slots=True)
class AppointmentDetail:
    appointment: Appointment
    patient_name: str
    doctor_name: str | None
    items: list[AppointmentItemRow]
