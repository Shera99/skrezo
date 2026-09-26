"""Границы периода в списке приёмов: дата — день целиком, время — точная граница.

``from``/``to`` принимают и дату (``2026-09-30``), и дату-время ISO. Дата
означает день целиком в поясе клиники: ``to=2026-09-30`` включает весь 30-е
число, а не обрывается на полуночи (раньше последний день выпадал). Время без
пояса тоже читается в поясе клиники, а не в поясе сервера. Верхняя граница
всегда исключающая: для даты — начало следующего дня, для времени — момент
сразу после указанного (прежнее «включительно» сохраняется).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.shared.db.models.clinic import DEFAULT_TIMEZONE

#: Точность timestamptz в Postgres — микросекунда: «≤ t» равно «< t + 1 мкс».
_TICK = timedelta(microseconds=1)


class InvalidPeriodBoundError(ValueError):
    """Граница периода — ни дата, ни дата-время ISO."""


def clinic_tz(timezone: str | None) -> ZoneInfo:
    """Таймзона клиники; битое значение — дефолт, выдачу не роняем."""
    try:
        return ZoneInfo(timezone or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError, ValueError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def period_start(raw: str, tz: ZoneInfo) -> datetime:
    """Нижняя граница (включительно): дата — начало дня клиники, время — как есть."""
    day = _as_date(raw)
    if day is not None:
        return datetime.combine(day, time.min, tzinfo=tz)
    return _as_datetime(raw, tz)


def period_end(raw: str, tz: ZoneInfo) -> datetime:
    """Верхняя граница (исключая): дата — начало следующего дня, время — сразу после."""
    day = _as_date(raw)
    if day is not None:
        return datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return _as_datetime(raw, tz) + _TICK


def _as_date(raw: str) -> date | None:
    value = raw.strip()
    if len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidPeriodBoundError(raw) from exc


def _as_datetime(raw: str, tz: ZoneInfo) -> datetime:
    try:
        moment = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise InvalidPeriodBoundError(raw) from exc
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=tz)
