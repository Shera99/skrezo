"""Доменные структуры чтения модуля приёмов."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.shared.db.enums import AppointmentStatus, AppointmentToothStatus, AppointmentType
from app.shared.db.models.appointment import Appointment


@dataclass(frozen=True, slots=True)
class ToothInput:
    tooth: int
    service_id: uuid.UUID | None
    price: int | None
    comment: str | None


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
    price_total: int


@dataclass(frozen=True, slots=True)
class AppointmentToothRow:
    id: uuid.UUID
    tooth_number: int
    service_name: str | None
    status: AppointmentToothStatus
    price: int
    comment: str | None


@dataclass(slots=True)
class AppointmentDetail:
    appointment: Appointment
    patient_name: str
    doctor_name: str | None
    teeth: list[AppointmentToothRow]
