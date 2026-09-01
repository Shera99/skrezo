"""Чистая арифметика интервалов: рабочее окно, вычитание занятого, нарезка слотов.

Здесь нет ни БД, ни FastAPI — только функции над :class:`TimeSpan`. Правила:

* интервалы полуоткрытые ``[start, end)`` — встык не считается пересечением;
* свободное время = окно врача минус обед, отсутствия и занятые приёмы;
* слот годится, если целиком помещается в свободный интервал.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.modules.schedule.domain.models import DoctorHours, TimeSpan, WorkingHours

# Длительность приёма, когда услуга не указана или у неё не задана длительность.
DEFAULT_DURATION_MIN = 30
# Шаг сетки предлагаемых слотов (09:00, 09:15, 09:30 …).
SLOT_STEP_MIN = 15
# Потолок множителя слота: больше 32 зубов (постоянный прикус) не бывает, а без
# границы `teethCount` из запроса растягивает слот на произвольную длину.
MAX_TEETH_COUNT = 32

_SUNDAY = 6

# Часы клиники по умолчанию — пока клиника не настроила своё расписание.
# Без них «лента дня» была бы пустой сразу после заведения клиники.
_DEFAULT_OPENS_AT = time(9, 0)
_DEFAULT_CLOSES_AT = time(18, 0)
_DEFAULT_BREAK_START = time(12, 0)
_DEFAULT_BREAK_END = time(13, 0)


def default_working_hours(weekday: int) -> WorkingHours:
    """Часы работы «по умолчанию»: пн–сб 09:00–18:00 с обедом 12:00–13:00, вс — выходной."""
    return WorkingHours(
        weekday=weekday,
        opens_at=_DEFAULT_OPENS_AT,
        closes_at=_DEFAULT_CLOSES_AT,
        break_start=_DEFAULT_BREAK_START,
        break_end=_DEFAULT_BREAK_END,
        is_working=weekday != _SUNDAY,
    )


def combine(day: date, moment: time, tz: ZoneInfo) -> datetime:
    """Собрать локальное время клиники в aware-datetime её таймзоны."""
    return datetime.combine(day, moment, tzinfo=tz)


def day_bounds(day: date, tz: ZoneInfo) -> TimeSpan:
    """Границы суток в таймзоне клиники (а не в UTC)."""
    start = datetime.combine(day, time.min, tzinfo=tz)
    return TimeSpan(start, start + timedelta(days=1))


def clinic_span(hours: WorkingHours, day: date, tz: ZoneInfo) -> TimeSpan | None:
    """Окно работы клиники в этот день; ``None`` — выходной или пустое окно."""
    if not hours.is_working or hours.opens_at >= hours.closes_at:
        return None
    return TimeSpan(combine(day, hours.opens_at, tz), combine(day, hours.closes_at, tz))


def break_span(hours: WorkingHours, day: date, tz: ZoneInfo) -> TimeSpan | None:
    """Полоса обеда клиники в этот день; ``None`` — обеда нет."""
    if hours.break_start is None or hours.break_end is None:
        return None
    if hours.break_start >= hours.break_end:
        return None
    return TimeSpan(combine(day, hours.break_start, tz), combine(day, hours.break_end, tz))


def doctor_span(hours: DoctorHours, day: date, tz: ZoneInfo) -> TimeSpan | None:
    """Окно индивидуального графика врача; ``None`` — не работает."""
    if not hours.is_working or hours.starts_at >= hours.ends_at:
        return None
    return TimeSpan(combine(day, hours.starts_at, tz), combine(day, hours.ends_at, tz))


def to_tz(span: TimeSpan, tz: ZoneInfo) -> TimeSpan:
    """Переложить интервал в таймзону клиники (момент времени не меняется).

    Из БД timestamptz приходит в UTC. Если не нормализовать, ответ смешивает
    два представления одного момента: сетка — ``09:00+06:00``, карточки — ``03:00Z``.
    """
    return TimeSpan(span.start.astimezone(tz), span.end.astimezone(tz))


def intersect(first: TimeSpan, second: TimeSpan) -> TimeSpan | None:
    """Пересечение двух интервалов; ``None`` — не пересекаются."""
    start = max(first.start, second.start)
    end = min(first.end, second.end)
    return TimeSpan(start, end) if start < end else None


def subtract(base: Sequence[TimeSpan], busy: Iterable[TimeSpan]) -> list[TimeSpan]:
    """Вычесть занятые интервалы из свободных, сохранив порядок."""
    result = list(base)
    for taken in busy:
        remaining: list[TimeSpan] = []
        for span in result:
            if not span.overlaps(taken):
                remaining.append(span)
                continue
            # Пересечение: оставляем «хвосты» слева и справа от занятого куска.
            if span.start < taken.start:
                remaining.append(TimeSpan(span.start, taken.start))
            if taken.end < span.end:
                remaining.append(TimeSpan(taken.end, span.end))
        result = remaining
    return result


def _align_up(moment: datetime, step: timedelta) -> datetime:
    """Ближайшая точка сетки шага (от локальной полуночи) не раньше ``moment``."""
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = moment - midnight
    steps = -(-elapsed // step)  # деление вверх
    return midnight + steps * step


def slice_slots(
    free: Sequence[TimeSpan],
    duration: timedelta,
    step: timedelta,
) -> list[TimeSpan]:
    """Нарезать свободные интервалы на слоты длиной ``duration`` с шагом ``step``.

    Слоты выравниваются по сетке шага от полуночи (09:00, 09:15, 09:30 …), а не
    от начала свободного куска: иначе приём 09:00–09:50 сдвигал бы всю оставшуюся
    сетку дня на 09:50, 10:05, 10:20 — регистратору такие времена не предложить.
    """
    if duration <= timedelta(0) or step <= timedelta(0):
        return []
    slots: list[TimeSpan] = []
    for span in free:
        start = _align_up(span.start, step)
        while start + duration <= span.end:
            slots.append(TimeSpan(start, start + duration))
            start += step
    return slots


def fits_window(window: TimeSpan | None, wanted: TimeSpan) -> bool:
    """Помещается ли приём целиком в рабочее окно."""
    return window is not None and window.contains(wanted)
