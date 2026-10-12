"""Доступность врача: рабочее окно, свободные интервалы, проверка записи.

Общий сервис для двух потребителей: ручек расписания (``GET /clinic/schedule/*``)
и записи на приём (``app/modules/appointments``) — чтобы «свободные слоты» и
«можно ли записать» считались по одним и тем же правилам.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
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
    doctor_free_time,
    doctor_window,
    fits_window,
)
from app.modules.schedule.domain.models import DoctorHours, TimeSpan, WorkingHours
from app.modules.schedule.infrastructure.repositories.schedule_repository import ScheduleRepository
from app.shared.db.models.clinic import DEFAULT_TIMEZONE


@dataclass(frozen=True, slots=True)
class DayContext:
    """Разобранный день клиники: таймзона, часы, график врача, его окно и обед."""

    tz: ZoneInfo
    hours: WorkingHours
    #: График врача на этот день недели; ``None`` — работает по часам клиники
    #: (или клиника в этот день закрыта, и график не поднимался).
    individual: DoctorHours | None
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
        individual: DoctorHours | None = None
        if membership_id is not None and clinic_span(hours, day, tz) is not None:
            # График врача нужен, только если клиника в этот день работает.
            doctor_hours = await self._schedule.doctor_hours(clinic_id, membership_id)
            individual = doctor_hours.get(day.weekday())
        return DayContext(
            tz=tz,
            hours=hours,
            individual=individual,
            window=doctor_window(hours, individual, day, tz),
            break_at=break_span(hours, day, tz),
        )

    async def free_spans(
        self, clinic_id: uuid.UUID, membership_id: uuid.UUID, day: date, tz: ZoneInfo
    ) -> list[TimeSpan]:
        """Свободное время врача за день: окно минус обед, отсутствия и занятые приёмы.

        Данные — запросами на этого врача и этот день; для многих врачей и дней
        есть :meth:`free_spans_many` с тем же правилом расчёта.
        """
        context = await self.day_context(clinic_id, membership_id, day, tz)
        if context.window is None:
            return []
        time_off = await self._schedule.time_off(clinic_id, [membership_id], day_bounds(day, tz))
        busy = await self._schedule.busy_spans(clinic_id, membership_id, context.window)
        return doctor_free_time(
            context.hours,
            context.individual,
            day,
            tz,
            [*time_off.get(membership_id, []), *busy],
        )

    async def free_spans_many(
        self,
        clinic_id: uuid.UUID,
        membership_ids: Sequence[uuid.UUID],
        days: Sequence[date],
        tz: ZoneInfo,
    ) -> dict[date, dict[uuid.UUID, list[TimeSpan]]]:
        """Свободное время каждого врача в каждый из ``days`` — четырьмя запросами.

        То же правило, что у :meth:`free_spans` (:func:`doctor_free_time`), но
        данные поднимаются заранее на весь период: часы клиники, графики всех
        врачей, их отсутствия и занятость. Дальше — чистая арифметика в памяти,
        без единого запроса в цикле: неделя на десяток врачей — не четыре сотни
        запросов, а четыре.
        """
        if not membership_ids or not days:
            return {day: {} for day in days}
        configured = await self._schedule.working_hours(clinic_id)
        individual_hours = await self._schedule.doctor_hours_many(clinic_id, membership_ids)
        period = TimeSpan(day_bounds(min(days), tz).start, day_bounds(max(days), tz).end)
        time_off = await self._schedule.time_off(clinic_id, membership_ids, period)
        busy = await self._schedule.busy_spans_many(clinic_id, membership_ids, period)

        result: dict[date, dict[uuid.UUID, list[TimeSpan]]] = {}
        for day in days:
            weekday = day.weekday()
            hours = configured.get(weekday) or default_working_hours(weekday)
            result[day] = {
                membership_id: doctor_free_time(
                    hours,
                    individual_hours.get(membership_id, {}).get(weekday),
                    day,
                    tz,
                    # Всё занятое за период: то, что не задевает окно дня,
                    # на расчёт не влияет.
                    [*time_off.get(membership_id, []), *busy.get(membership_id, [])],
                )
                for membership_id in membership_ids
            }
        return result

    async def working_minutes(
        self,
        clinic_id: uuid.UUID,
        membership_ids: Sequence[uuid.UUID],
        date_from: date,
        date_to: date,
        tz: ZoneInfo,
    ) -> dict[uuid.UUID, int]:
        """Рабочие минуты каждого врача за период ``[date_from, date_to]`` включительно.

        Та же арифметика, что у свободных слотов: окно дня = часы клиники ∩
        график врача, минус обед и отсутствия. Считается по календарю графика, а
        не по приёмам — это знаменатель загрузки врача в отчётах. Данные
        поднимаются заранее (часы клиники, графики всех врачей, отсутствия за
        период), дальше чистая арифметика: квартал на десяток врачей — три
        запроса, без единого в цикле.
        """
        result = dict.fromkeys(membership_ids, 0)
        if not membership_ids or date_to < date_from:
            return result
        configured = await self._schedule.working_hours(clinic_id)
        individual_hours = await self._schedule.doctor_hours_many(clinic_id, membership_ids)
        period = TimeSpan(day_bounds(date_from, tz).start, day_bounds(date_to, tz).end)
        time_off = await self._schedule.time_off(clinic_id, membership_ids, period)

        day = date_from
        while day <= date_to:
            weekday = day.weekday()
            hours = configured.get(weekday) or default_working_hours(weekday)
            if clinic_span(hours, day, tz) is not None:
                for membership_id in membership_ids:
                    free = doctor_free_time(
                        hours,
                        individual_hours.get(membership_id, {}).get(weekday),
                        day,
                        tz,
                        time_off.get(membership_id, []),
                    )
                    result[membership_id] += sum(_minutes(span) for span in free)
            day += timedelta(days=1)
        return result

    async def ensure_bookable(
        self,
        clinic_id: uuid.UUID,
        membership_id: uuid.UUID,
        wanted: TimeSpan,
        *,
        exclude_appointment_id: uuid.UUID | None = None,
        force: bool = False,
    ) -> None:
        """Проверить, что врача можно записать на ``wanted``.

        Нерабочее время (вне окна, обед, отсутствие) → 422; занятый слот → 409.
        Констрейнт ``ex_appointment_doctor_overlap`` страхует от гонки, но
        человекочитаемую ошибку даёт этот метод.

        ``force`` снимает **только** проверку рабочих часов и обеда: клиники
        принимают вне графика постоянно, и жёсткий запрет приводит лишь к тому,
        что такой приём не заведут в системе вовсе. Пересечение приёмов и отпуск
        врача не обходятся никогда — это не неудобное правило, а двойная запись
        и работа в отсутствие врача.
        """
        tz = await self.clinic_timezone(clinic_id)
        day = wanted.start.astimezone(tz).date()

        if not force:
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
        *,
        units: int = 1,
    ) -> int:
        """Длительность приёма: явная → длительность услуги × ``units`` → дефолт.

        ``units`` — сколько раз процедура выполняется за приём (сколько зубов):
        длительность услуги клиника задаёт на ОДИН зуб, поэтому работа по трём
        зубам занимает втрое больше времени. По умолчанию 1 — поведение прежних
        вызовов не меняется.
        """
        if explicit is not None:
            return explicit
        units = max(units, 1)
        if service_id is not None:
            _, duration = await self._schedule.service_duration(clinic_id, service_id)
            if duration:
                return duration * units
        return DEFAULT_DURATION_MIN * units


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


def _minutes(span: TimeSpan) -> int:
    """Длина интервала в целых минутах."""
    return int((span.end - span.start).total_seconds() // 60)
