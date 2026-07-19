"""Схемы модуля расписания.

Времена сетки и карточек — aware-datetime в таймзоне клиники (поле ``timezone``),
чтобы фронт позиционировал карточки без собственных пересчётов.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

from pydantic import AwareDatetime, Field, model_validator

from app.modules.schedule.domain.models import (
    AppointmentBlock,
    DayCalendar,
    DaySlots,
    DoctorColumn,
    DoctorHours,
    TimeOffList,
    TimeOffRecord,
    TimeSpan,
    WorkingHours,
)
from app.shared.api.schemas import CamelModel
from app.shared.db.enums import AppointmentStatus, AppointmentType


class SpanOut(CamelModel):
    """Интервал ``[startAt, endAt)``."""

    start_at: datetime = Field(
        description="Начало интервала", examples=["2026-07-20T12:00:00+06:00"]
    )
    end_at: datetime = Field(
        description="Конец интервала", examples=["2026-07-20T13:00:00+06:00"]
    )

    @classmethod
    def from_span(cls, span: TimeSpan) -> SpanOut:
        return cls(start_at=span.start, end_at=span.end)


class DoctorColumnOut(CamelModel):
    """Колонка врача в «ленте дня»."""

    membership_id: uuid.UUID = Field(description="Врач = членство в клинике")
    full_name: str = Field(description="ФИО врача", examples=["Иванов Иван"])
    specialization_name: str | None = Field(default=None, examples=["Терапевт"])
    appointments_count: int = Field(description="Приёмов за день", examples=[7])
    works_from: datetime | None = Field(default=None, description="Начало смены")
    works_to: datetime | None = Field(default=None, description="Конец смены")
    is_working: bool = Field(description="Работает ли в этот день (с учётом отсутствий)")

    @classmethod
    def from_column(cls, c: DoctorColumn) -> DoctorColumnOut:
        return cls(
            membership_id=c.membership_id,
            full_name=c.full_name,
            specialization_name=c.specialization_name,
            appointments_count=c.appointments_count,
            works_from=c.works_from,
            works_to=c.works_to,
            is_working=c.is_working,
        )


class AppointmentBlockOut(CamelModel):
    """Карточка приёма на сетке."""

    id: uuid.UUID
    doctor_membership_id: uuid.UUID | None = None
    patient_id: uuid.UUID
    patient_name: str = Field(examples=["Петров Пётр"])
    service_name: str | None = Field(default=None, examples=["Лечение кариеса"])
    start_at: datetime = Field(examples=["2026-07-20T09:00:00+06:00"])
    end_at: datetime = Field(
        description="= начало + duration_min", examples=["2026-07-20T09:45:00+06:00"]
    )
    status: AppointmentStatus
    type: AppointmentType = Field(description="Тип приёма")

    @classmethod
    def from_block(cls, b: AppointmentBlock) -> AppointmentBlockOut:
        return cls(
            id=b.id,
            doctor_membership_id=b.doctor_membership_id,
            patient_id=b.patient_id,
            patient_name=b.patient_name,
            service_name=b.service_name,
            start_at=b.start_at,
            end_at=b.end_at,
            status=b.status,
            type=b.appointment_type,
        )


class ScheduleDayResponse(CamelModel):
    """Всё для отрисовки экрана «Сегодня»."""

    date: date
    timezone: str = Field(description="IANA-таймзона клиники", examples=["Asia/Bishkek"])
    opens_at: datetime | None = Field(default=None, description="Открытие клиники в этот день")
    closes_at: datetime | None = Field(default=None, description="Закрытие клиники в этот день")
    is_working: bool = Field(description="Рабочий ли это день для клиники")
    break_: SpanOut | None = Field(
        default=None, alias="break", description="Общая полоса обеда или null"
    )
    doctors: list[DoctorColumnOut]
    appointments: list[AppointmentBlockOut]

    @classmethod
    def from_calendar(cls, c: DayCalendar) -> ScheduleDayResponse:
        return cls(
            date=c.day,
            timezone=c.timezone,
            opens_at=c.opens_at,
            closes_at=c.closes_at,
            is_working=c.is_working,
            break_=SpanOut.from_span(c.break_span) if c.break_span is not None else None,
            doctors=[DoctorColumnOut.from_column(d) for d in c.doctors],
            appointments=[AppointmentBlockOut.from_block(a) for a in c.appointments],
        )


class ScheduleSlotsResponse(CamelModel):
    """Свободные слоты врача на день."""

    date: date
    timezone: str = Field(examples=["Asia/Bishkek"])
    doctor_id: uuid.UUID = Field(description="Членство врача")
    duration_min: int = Field(description="Длительность слота, минуты", examples=[45])
    slots: list[SpanOut]

    @classmethod
    def from_day_slots(cls, s: DaySlots) -> ScheduleSlotsResponse:
        return cls(
            date=s.day,
            timezone=s.timezone,
            doctor_id=s.doctor_membership_id,
            duration_min=s.duration_min,
            slots=[SpanOut.from_span(x) for x in s.slots],
        )


class WorkingHoursItem(CamelModel):
    """Часы работы клиники в один день недели."""

    weekday: int = Field(ge=0, le=6, description="0=понедельник … 6=воскресенье", examples=[0])
    opens_at: time = Field(examples=["09:00:00"])
    closes_at: time = Field(examples=["18:00:00"])
    break_start: time | None = Field(default=None, examples=["12:00:00"])
    break_end: time | None = Field(default=None, examples=["13:00:00"])
    is_working: bool = Field(default=True)

    @classmethod
    def from_hours(cls, h: WorkingHours) -> WorkingHoursItem:
        return cls(
            weekday=h.weekday,
            opens_at=h.opens_at,
            closes_at=h.closes_at,
            break_start=h.break_start,
            break_end=h.break_end,
            is_working=h.is_working,
        )

    def to_hours(self) -> WorkingHours:
        return WorkingHours(
            weekday=self.weekday,
            opens_at=self.opens_at,
            closes_at=self.closes_at,
            break_start=self.break_start,
            break_end=self.break_end,
            is_working=self.is_working,
        )


class WorkingHoursResponse(CamelModel):
    """Часы работы клиники — всегда ровно 7 дней."""

    days: list[WorkingHoursItem]


class SetWorkingHoursRequest(CamelModel):
    """Замена часов работы: передаются все 7 дней разом."""

    days: list[WorkingHoursItem] = Field(min_length=7, max_length=7)


class DoctorHoursItem(CamelModel):
    """График врача в один день недели."""

    weekday: int = Field(ge=0, le=6, description="0=понедельник … 6=воскресенье", examples=[0])
    starts_at: time = Field(examples=["09:00:00"])
    ends_at: time = Field(examples=["15:00:00"])
    is_working: bool = Field(default=True)

    @classmethod
    def from_hours(cls, h: DoctorHours) -> DoctorHoursItem:
        return cls(
            weekday=h.weekday,
            starts_at=h.starts_at,
            ends_at=h.ends_at,
            is_working=h.is_working,
        )

    def to_hours(self) -> DoctorHours:
        return DoctorHours(
            weekday=self.weekday,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            is_working=self.is_working,
        )


class DoctorHoursResponse(CamelModel):
    """График врача — всегда ровно 7 дней."""

    days: list[DoctorHoursItem]


class SetDoctorHoursRequest(CamelModel):
    """Замена графика врача: передаются все 7 дней разом."""

    days: list[DoctorHoursItem] = Field(min_length=7, max_length=7)


class CreateTimeOffRequest(CamelModel):
    """Новое отсутствие врача (отпуск, больничный, разовая недоступность).

    Интервал абсолютный и aware: с явным смещением таймзоны, как ``scheduledAt``
    у приёма. Полуоткрытый ``[startsAt, endsAt)``, конец строго позже начала.
    """

    starts_at: AwareDatetime = Field(
        description="Начало отсутствия (с таймзоной)", examples=["2026-07-20T00:00:00+06:00"]
    )
    ends_at: AwareDatetime = Field(
        description="Конец отсутствия (с таймзоной)", examples=["2026-07-21T00:00:00+06:00"]
    )
    reason: str | None = Field(
        default=None, max_length=500, description="Причина", examples=["Отпуск"]
    )

    @model_validator(mode="after")
    def _check_range(self) -> CreateTimeOffRequest:
        if self.ends_at <= self.starts_at:
            raise ValueError("Конец отсутствия должен быть позже начала")
        return self


class TimeOffItem(CamelModel):
    """Отсутствие врача как элемент списка."""

    id: uuid.UUID
    membership_id: uuid.UUID = Field(description="Врач = членство в клинике")
    starts_at: datetime = Field(examples=["2026-07-20T00:00:00+06:00"])
    ends_at: datetime = Field(examples=["2026-07-21T00:00:00+06:00"])
    reason: str | None = Field(default=None, examples=["Отпуск"])

    @classmethod
    def from_record(cls, r: TimeOffRecord) -> TimeOffItem:
        return cls(
            id=r.id,
            membership_id=r.membership_id,
            starts_at=r.starts_at,
            ends_at=r.ends_at,
            reason=r.reason,
        )


class TimeOffListResponse(CamelModel):
    """Отсутствия врача в таймзоне клиники."""

    timezone: str = Field(description="IANA-таймзона клиники", examples=["Asia/Bishkek"])
    doctor_id: uuid.UUID = Field(description="Членство врача")
    items: list[TimeOffItem]

    @classmethod
    def from_list(cls, result: TimeOffList) -> TimeOffListResponse:
        return cls(
            timezone=result.timezone,
            doctor_id=result.doctor_membership_id,
            items=[TimeOffItem.from_record(r) for r in result.records],
        )


class TimeOffDeletedResponse(CamelModel):
    """Подтверждение удаления отсутствия."""

    id: uuid.UUID = Field(description="Id удалённого отсутствия")
    deleted: bool = True
