"""Доменные структуры чтения модуля пациентов (глобальный пациент)."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from app.shared.db.enums import AppointmentItemStatus, ToothStatus
from app.shared.db.models.patient import Patient
from app.shared.db.models.patient_file import PatientFile
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
    #: Последняя отметка «напомнили» в этой клинике — чтобы экран не звонил дважды.
    last_reminder_at: datetime | None = None


class FileSource(enum.StrEnum):
    """Чьи файлы пациента показать: своей клиники, других или все."""

    OWN = "own"
    OTHERS = "others"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class PatientFileView:
    """Файл пациента вместе с названием клиники, которая его загрузила."""

    record: PatientFile
    clinic_name: str


@dataclass(frozen=True, slots=True)
class ToothFilesCount:
    """Сколько файлов у зуба — своей клиники и других."""

    tooth: int
    own: int
    others: int


@dataclass(frozen=True, slots=True)
class PatientFilesSummary:
    """Счётчики файлов для карточки: всего и по зубам (метки в формуле)."""

    own: int = 0
    others: int = 0
    teeth: list[ToothFilesCount] = field(default_factory=list)


@dataclass(slots=True)
class PatientCard:
    patient: Patient
    odontogram: list[PatientTooth]
    appointments_count: int
    files: PatientFilesSummary


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
