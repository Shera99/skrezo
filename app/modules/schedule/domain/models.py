"""Доменные структуры модуля расписания.

Все ``datetime`` здесь — aware и живут в таймзоне клиники: границы дня, сетка и
карточки приёмов считаются именно в ней, а не в UTC.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time

from app.shared.db.enums import AppointmentStatus, AppointmentType


@dataclass(frozen=True, slots=True)
class TimeSpan:
    """Полуоткрытый интервал ``[start, end)`` — как ``tstzrange`` в БД.

    Полуоткрытость важна: приём 09:00–09:45 и приём 09:45–10:15 идут встык и
    пересечением не считаются.
    """

    start: datetime
    end: datetime

    def overlaps(self, other: TimeSpan) -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, other: TimeSpan) -> bool:
        return self.start <= other.start and other.end <= self.end


@dataclass(frozen=True, slots=True)
class WorkingHours:
    """Часы работы клиники в конкретный день недели (0=пн … 6=вс)."""

    weekday: int
    opens_at: time
    closes_at: time
    break_start: time | None
    break_end: time | None
    is_working: bool


@dataclass(frozen=True, slots=True)
class DoctorHours:
    """Индивидуальный график врача в конкретный день недели."""

    weekday: int
    starts_at: time
    ends_at: time
    is_working: bool


@dataclass(frozen=True, slots=True)
class DoctorRow:
    """Врач как колонка «ленты дня» (активное членство со специализацией)."""

    membership_id: uuid.UUID
    full_name: str
    specialization_name: str | None


@dataclass(frozen=True, slots=True)
class DoctorColumn:
    """Колонка календаря: врач + его рабочее окно, отсутствия и счётчик приёмов.

    Отсутствия отдаются целиком, а не только как признак нерабочего дня: врач,
    уехавший на два часа, остаётся рабочим, и без этих интервалов на сетке нечем
    показать дыру — записать в неё всё равно нельзя.
    """

    membership_id: uuid.UUID
    full_name: str
    specialization_name: str | None
    appointments_count: int
    works_from: datetime | None
    works_to: datetime | None
    is_working: bool
    #: Отсутствия, попадающие в этот день (в т.ч. накрывающие его целиком —
    #: тогда ``is_working`` уже false, а интервал объясняет, почему).
    time_off: tuple[TimeSpan, ...] = ()


@dataclass(frozen=True, slots=True)
class AppointmentBlock:
    """Карточка приёма на сетке дня."""

    id: uuid.UUID
    doctor_membership_id: uuid.UUID | None
    patient_id: uuid.UUID
    patient_name: str
    service_name: str | None
    start_at: datetime
    end_at: datetime
    status: AppointmentStatus
    appointment_type: AppointmentType


@dataclass(frozen=True, slots=True)
class DayCalendar:
    """Всё, что нужно для отрисовки экрана «Сегодня»."""

    day: date
    timezone: str
    opens_at: datetime | None
    closes_at: datetime | None
    is_working: bool
    break_span: TimeSpan | None
    doctors: list[DoctorColumn]
    appointments: list[AppointmentBlock]


@dataclass(frozen=True, slots=True)
class DaySlots:
    """Свободные слоты врача на день."""

    day: date
    timezone: str
    doctor_membership_id: uuid.UUID
    duration_min: int
    slots: list[TimeSpan]


@dataclass(frozen=True, slots=True)
class TimeOffRecord:
    """Отсутствие врача как отдельная запись — для списка и точечного удаления.

    В отличие от :class:`TimeSpan` (безымянный интервал для расчёта доступности)
    несёт ``id`` и причину, поэтому пригодна как элемент CRUD-выдачи.
    """

    id: uuid.UUID
    membership_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    reason: str | None


@dataclass(frozen=True, slots=True)
class TimeOffList:
    """Отсутствия врача с таймзоной клиники для отрисовки без пересчётов на фронте."""

    timezone: str
    doctor_membership_id: uuid.UUID
    records: list[TimeOffRecord]
