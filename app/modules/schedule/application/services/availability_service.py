"""Доступность врача: рабочее окно, свободные интервалы, проверка записи.

Общий сервис для двух потребителей: ручек расписания (``GET /clinic/schedule/*``)
и записи на приём (``app/modules/appointments``) — чтобы «свободные слоты» и
«можно ли записать» считались по одним и тем же правилам.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.modules.schedule.domain.availability import (
    DEFAULT_DURATION_MIN,
    break_span,
    clinic_span,
    day_bounds,
    default_working_hours,
    doctor_span,
    fits_window,
    intersect,
    subtract,
    to_tz,
)
from app.modules.schedule.domain.models import TimeSpan, WorkingHours
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository
from app.shared.db.models.clinic import DEFAULT_TIMEZONE


@dataclass(frozen=True, slots=True)
class DayContext:
    """Разобранный день клиники: таймзона, часы, окно врача и обед."""

    tz: ZoneInfo
    hours: WorkingHours
    window: TimeSpan | None
    break_at: TimeSpan | None


class AvailabilityService:
    def __init__(self, schedule: ScheduleRepository) -> None:
        self._schedule = schedule

    async def clinic_timezone(self, clinic_id: uuid.UUID) -> ZoneInfo:
        """Таймзона клиники; при битом значении в БД — дефолт, чтобы не ронять выдачу."""
        clinic = await self._schedule.get_clinic(clinic_id)
        if clinic is None:
            raise NotFoundError("Клиника не найдена", code="clinic_not_found")
        try:
            return ZoneInfo(clinic.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo(DEFAULT_TIMEZONE)

    async def is_doctor(self, clinic_id: uuid.UUID, membership_id: uuid.UUID) -> bool:
        """Практикующий врач этой клиники: активное членство со специализацией.

        Единственное определение «врача» на весь проект: по нему строятся колонки
        календаря, и по нему же проверяется запись на приём. Иначе можно записать
        на уволенного врача или на владельца без специализации — приём попадёт в
        ``appointments[]``, а колонки для него в «ленте дня» не будет.
        """
        return await self._schedule.is_doctor_in_clinic(clinic_id, membership_id)

    async def clinic_hours(self, clinic_id: uuid.UUID, weekday: int) -> WorkingHours:
        """Часы клиники на день недели; если не настроены — разумный дефолт."""
        configured = await self._schedule.working_hours(clinic_id)
        return configured.get(weekday) or default_working_hours(weekday)

    async def day_context(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID | None, day: date, tz: ZoneInfo
    ) -> DayContext:
        """Окно приёма врача на день = часы клиники ∩ график врача (если задан)."""
        hours = await self.clinic_hours(clinic_id, day.weekday())
        window = clinic_span(hours, day, tz)
        if window is not None and membership_id is not None:
            doctor_hours = await self._schedule.doctor_hours(clinic_id, membership_id)
            individual = doctor_hours.get(day.weekday())
            if individual is not None:
                span = doctor_span(individual, day, tz)
                window = intersect(window, span) if span is not None else None
        return DayContext(tz=tz, hours=hours, window=window, break_at=break_span(hours, day, tz))

    async def free_spans(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID, day: date, tz: ZoneInfo
    ) -> list[TimeSpan]:
        """Свободное время врача за день: окно минус обед, отсутствия и занятые приёмы."""
        context = await self.day_context(clinic_id, membership_id, day, tz)
        if context.window is None:
            return []

        blocked: list[TimeSpan] = []
        if context.break_at is not None:
            blocked.append(context.break_at)
        time_off = await self._schedule.time_off(clinic_id, [membership_id], day_bounds(day, tz))
        blocked.extend(time_off.get(membership_id, []))
        blocked.extend(await self._schedule.busy_spans(clinic_id, membership_id, context.window))
        # Границы свободных интервалов наследуются от занятых, а те приходят из БД
        # в UTC — нормализуем, чтобы слоты отдавались в таймзоне клиники.
        return subtract([context.window], [to_tz(span, tz) for span in blocked])

    async def ensure_bookable(
        self,
        clinic_id: uuid.UUID,
        membership_id: uuid.UUID,
        wanted: TimeSpan,
        *,
        exclude_appointment_id: uuid.UUID | None = None,
    ) -> None:
        """Проверить, что врача можно записать на ``wanted``.

        Нерабочее время (вне окна, обед, отсутствие) → 422; занятый слот → 409.
        Констрейнт ``ex_appointment_doctor_overlap`` страхует от гонки, но
        человекочитаемую ошибку даёт этот метод.
        """
        tz = await self.clinic_timezone(clinic_id)
        day = wanted.start.astimezone(tz).date()
        context = await self.day_context(clinic_id, membership_id, day, tz)

        if not fits_window(context.window, wanted):
            raise ValidationAppError(
                "Время вне рабочего окна врача", code="outside_working_hours"
            )
        if context.break_at is not None and context.break_at.overlaps(wanted):
            raise ValidationAppError("Время попадает на обед", code="break_time")

        time_off = await self._schedule.time_off(clinic_id, [membership_id], wanted)
        if any(span.overlaps(wanted) for span in time_off.get(membership_id, [])):
            raise ValidationAppError("Врач отсутствует в это время", code="doctor_time_off")

        busy = await self._schedule.busy_spans(
            clinic_id, membership_id, wanted, exclude_appointment_id=exclude_appointment_id
        )
        if busy:
            raise ConflictError("У врача уже есть приём в это время", code="slot_taken")

    async def resolve_duration(
        self,
        clinic_id: uuid.UUID,
        service_id: uuid.UUID | None,
        explicit: int | None,
    ) -> int:
        """Длительность приёма: явная → из услуги → дефолт."""
        if explicit is not None:
            return explicit
        if service_id is not None:
            _, duration = await self._schedule.service_duration(clinic_id, service_id)
            if duration:
                return duration
        return DEFAULT_DURATION_MIN


def build_availability_service(session: AsyncSession) -> AvailabilityService:
    """Собрать сервис доступности поверх сессии.

    Провайдер живёт в application-слое, чтобы соседние модули (``appointments``)
    брали сервис отсюда, а не импортировали ``schedule.presentation.deps``:
    presentation одного модуля не должен зависеть от ручек другого.
    """
    return AvailabilityService(ScheduleRepository(session))


def span_of(start: datetime, duration_min: int) -> TimeSpan:
    """Собрать интервал приёма из времени начала и длительности в минутах."""
    return TimeSpan(start, start + timedelta(minutes=duration_min))
