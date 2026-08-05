"""Период отчёта: пресеты (7 дней / 1 месяц / 3 месяца / всё время) и границы.

Пресет разворачивается в полуоткрытый диапазон ``[start, end)`` **в таймзоне
клиники** и переводится в UTC — под фильтры по ``timestamptz``. День ``to``
входит целиком: верхняя граница — начало следующих суток.

Явные ``from``/``to`` (произвольный диапазон) перекрывают пресет.
"""

from __future__ import annotations

import calendar
import enum
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.shared.db.models.clinic import DEFAULT_TIMEZONE


class ReportPeriod(enum.StrEnum):
    """Пресеты периода отчёта."""

    WEEK = "7d"  # последние 7 дней, включая сегодня
    MONTH = "1m"  # календарный месяц назад
    QUARTER = "3m"  # три календарных месяца назад
    ALL = "all"  # за всё время (без нижней границы)


def resolve_tz(timezone: str) -> ZoneInfo:
    """Таймзона клиники; при битом значении — дефолт платформы."""
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError, ValueError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _minus_months(day: date, months: int) -> date:
    """Отнять календарные месяцы, поджимая день к длине месяца (31 марта − 1 мес = 28/29 фев)."""
    total = day.year * 12 + (day.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    last_day = calendar.monthrange(year, month)[1]
    return day.replace(year=year, month=month, day=min(day.day, last_day))


@dataclass(frozen=True, slots=True)
class PeriodBounds:
    """Границы отчёта в UTC (``start`` = ``None`` для «за всё время») и их подпись."""

    start: datetime | None
    end: datetime
    period: ReportPeriod | None
    date_from: date | None
    date_to: date


def _to_utc(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def resolve_period(
    period: ReportPeriod | None,
    date_from: date | None,
    date_to: date | None,
    tz: ZoneInfo,
    *,
    today: date | None = None,
) -> PeriodBounds:
    """Свести пресет и/или явный диапазон к UTC-границам.

    Явные даты приоритетнее пресета: переданный ``from``/``to`` задаёт
    произвольный период. Если не передано ничего — берётся ``1m``.
    """
    current = today or datetime.now(tz).date()
    if date_from is not None or date_to is not None:
        start_day = date_from
        end_day = date_to or current
        return PeriodBounds(
            start=_to_utc(start_day, tz) if start_day is not None else None,
            end=_to_utc(end_day + timedelta(days=1), tz),
            period=None,
            date_from=start_day,
            date_to=end_day,
        )

    preset = period or ReportPeriod.MONTH
    if preset is ReportPeriod.ALL:
        start_day = None
    elif preset is ReportPeriod.WEEK:
        start_day = current - timedelta(days=6)  # включая сегодня — ровно 7 дней
    elif preset is ReportPeriod.MONTH:
        start_day = _minus_months(current, 1)
    else:
        start_day = _minus_months(current, 3)

    return PeriodBounds(
        start=_to_utc(start_day, tz) if start_day is not None else None,
        end=_to_utc(current + timedelta(days=1), tz),
        period=preset,
        date_from=start_day,
        date_to=current,
    )
