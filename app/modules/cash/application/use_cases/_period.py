"""Разбор периода журнала кассы: даты клиники → UTC-границы, вывод в tz клиники.

``from``/``to`` приходят как даты в таймзоне клиники (её «сегодня» — в её tz).
Полуоткрытый диапазон ``[начало from, начало (to+1 день))``, чтобы день ``to``
включался целиком. Границы переводятся в UTC — под фильтр ``created_at`` (tstz).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.shared.db.models.clinic import DEFAULT_TIMEZONE


def resolve_tz(timezone: str) -> ZoneInfo:
    """Таймзона клиники; при битом значении — дефолт, чтобы не ронять выдачу."""
    try:
        return ZoneInfo(timezone)
    except ZoneInfoNotFoundError, ValueError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def period_bounds(
    date_from: date | None, date_to: date | None, tz: ZoneInfo
) -> tuple[datetime | None, datetime | None]:
    """Даты клиники → полуоткрытые UTC-границы ``[from_start, to_end)``."""
    start = (
        datetime.combine(date_from, time.min, tzinfo=tz).astimezone(UTC)
        if date_from is not None
        else None
    )
    end = (
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
        if date_to is not None
        else None
    )
    return start, end
