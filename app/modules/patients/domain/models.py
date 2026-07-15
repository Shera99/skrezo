"""Доменные структуры чтения модуля пациентов."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from app.shared.db.models.patient import Patient
from app.shared.db.models.tooth_record import ToothRecord
from app.shared.db.models.visit import Visit


@dataclass(frozen=True, slots=True)
class PatientListRow:
    id: uuid.UUID
    full_name: str
    phone: str | None
    birth_date: date | None
    gender: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OdontogramTooth:
    """Текущее состояние зуба (последняя запись) + счётчик истории."""

    tooth: int
    status: str | None
    history_count: int
    last_at: datetime


@dataclass(slots=True)
class PatientDetail:
    patient: Patient
    odontogram: list[OdontogramTooth]
    visits_count: int


@dataclass(frozen=True, slots=True)
class VisitRow:
    id: uuid.UUID
    visited_at: datetime
    doctor_name: str | None
    complaint: str | None
    records_count: int


@dataclass(slots=True)
class VisitDetail:
    visit: Visit
    doctor_name: str | None
    records: list[ToothRecord]
