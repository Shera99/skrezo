"""Доменные структуры чтения модуля пациентов (глобальный пациент)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from app.shared.db.enums import AppointmentItemStatus, ToothStatus
from app.shared.db.models.patient import Patient
from app.shared.db.models.patient_tooth import PatientTooth


@dataclass(frozen=True, slots=True)
class PatientListRow:
    id: uuid.UUID
    phone: str
    first_name: str
    last_name: str | None
    birth_date: date | None
    gender: str | None
    #: Последний завершённый приём в этой клинике — по нему «Напоминания» зовут
    #: на повторный осмотр.
    last_visit_at: datetime | None = None
    #: Долг пациента в этой клинике: сумма остатков завершённых приёмов.
    debt_tiyin: int = 0


@dataclass(slots=True)
class PatientCard:
    patient: Patient
    odontogram: list[PatientTooth]
    appointments_count: int


@dataclass(frozen=True, slots=True)
class ToothHistoryEntry:
    """Одна запись истории зуба (сквозная по всем клиникам)."""

    appointment_id: uuid.UUID
    date: datetime
    clinic_name: str
    doctor_name: str | None
    service_name: str | None
    status: AppointmentItemStatus
    price_tiyin: int
    comment: str | None


@dataclass(frozen=True, slots=True)
class ToothStatePatch:
    tooth_number: int
    status: ToothStatus
    note: str | None
