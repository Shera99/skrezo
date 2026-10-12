"""Юнит-тесты единого правила свободного времени врача (без БД).

``doctor_free_time`` считают и слоты одного врача на день, и пакетный подбор
по многим врачам и дням, и рабочие минуты отчётов. Пакетный подбор передаёт
всё занятое врача за ПЕРИОД, не разрезая по дням, — поэтому здесь отдельно
закреплено: интервалы вне окна дня и порядок занятых на результат не влияют.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.modules.schedule.domain.availability import (
    doctor_free_time,
    doctor_slots,
    doctor_window,
)
from app.modules.schedule.domain.models import DoctorHours, TimeSpan, WorkingHours

_TZ = ZoneInfo("Asia/Bishkek")  # UTC+6
_MONDAY = date(2026, 7, 20)


def _at(hhmm: str, day: date = _MONDAY) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=_TZ)


def _span(start: str, end: str, day: date = _MONDAY) -> TimeSpan:
    return TimeSpan(_at(start, day), _at(end, day))


def _hours(*, working: bool = True, lunch: bool = True) -> WorkingHours:
    return WorkingHours(
        weekday=_MONDAY.weekday(),
        opens_at=time(9, 0),
        closes_at=time(18, 0),
        break_start=time(13, 0) if lunch else None,
        break_end=time(14, 0) if lunch else None,
        is_working=working,
    )


def _individual(starts: str, ends: str, *, working: bool = True) -> DoctorHours:
    return DoctorHours(
        weekday=_MONDAY.weekday(),
        starts_at=time.fromisoformat(starts),
        ends_at=time.fromisoformat(ends),
        is_working=working,
    )


def test_window_is_clinic_hours_intersected_with_doctor_schedule() -> None:
    assert doctor_window(_hours(), None, _MONDAY, _TZ) == _span("09:00", "18:00")
    assert doctor_window(_hours(), _individual("12:00", "21:00"), _MONDAY, _TZ) == _span(
        "12:00", "18:00"
    )
    # Выходной врача, выходной клиники, графики без пересечения — окна нет.
    assert (
        doctor_window(_hours(), _individual("12:00", "21:00", working=False), _MONDAY, _TZ) is None
    )
    assert doctor_window(_hours(working=False), None, _MONDAY, _TZ) is None
    assert doctor_window(_hours(), _individual("19:00", "21:00"), _MONDAY, _TZ) is None


def test_free_time_subtracts_lunch_and_blocked_and_returns_clinic_tz() -> None:
    # Приём из БД приходит в UTC: 04:00Z = 10:00 по Бишкеку.
    busy = TimeSpan(
        datetime(2026, 7, 20, 4, 0, tzinfo=UTC), datetime(2026, 7, 20, 4, 50, tzinfo=UTC)
    )
    free = doctor_free_time(_hours(), None, _MONDAY, _TZ, [busy])

    assert free == [_span("09:00", "10:00"), _span("10:50", "13:00"), _span("14:00", "18:00")]
    assert all(span.start.tzinfo == _TZ and span.end.tzinfo == _TZ for span in free)


def test_free_time_ignores_spans_outside_the_day_and_their_order() -> None:
    """Всё занятое за неделю даёт то же, что занятое только этого дня."""
    today = [_span("09:30", "10:00"), _span("15:00", "16:30")]
    elsewhere = [
        _span("10:00", "12:00", _MONDAY - timedelta(days=1)),
        _span("09:00", "18:00", _MONDAY + timedelta(days=1)),
    ]
    # Приём с вечера накануне до 09:15 — задевает окно и вычитается.
    overnight = TimeSpan(_at("22:00", _MONDAY - timedelta(days=1)), _at("09:15"))

    expected = doctor_free_time(_hours(lunch=False), None, _MONDAY, _TZ, [overnight, *today])
    shuffled = [today[1], *elsewhere, overnight, today[0]]

    assert doctor_free_time(_hours(lunch=False), None, _MONDAY, _TZ, shuffled) == expected
    assert expected == [_span("09:15", "09:30"), _span("10:00", "15:00"), _span("16:30", "18:00")]


def test_free_time_is_empty_without_window() -> None:
    assert doctor_free_time(_hours(working=False), None, _MONDAY, _TZ, []) == []


def test_doctor_slots_merge_by_time_keeping_doctor_order_on_ties() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    slots = doctor_slots(
        [(first, [_span("10:00", "11:00")]), (second, [_span("09:30", "10:30")])],
        duration_min=30,
    )

    assert [(s.span.start.strftime("%H:%M"), s.doctor_membership_id) for s in slots] == [
        ("09:30", second),
        ("09:45", second),
        ("10:00", first),
        ("10:00", second),
        ("10:15", first),
        ("10:30", first),
    ]
