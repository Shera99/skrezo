"""Оплаченный период: продление на N месяцев и деньги провайдера."""

from __future__ import annotations

import calendar
from datetime import datetime

MIN_MONTHS = 1
MAX_MONTHS = 12
CURRENCY = "KGS"


def add_months(moment: datetime, months: int) -> datetime:
    """Плюс календарные месяцы, поджимая день к длине месяца (31.01 + 1 = 28/29.02)."""
    total = moment.year * 12 + (moment.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def next_period(
    now: datetime, current_end: datetime | None, months: int
) -> tuple[datetime, datetime]:
    """Новый период начинается там, где кончается оплаченный, если он ещё идёт."""
    start = current_end if current_end is not None and current_end > now else now
    return start, add_months(start, months)


def som_amount(amount_tiyin: int) -> str:
    """Сумма провайдеру — сомы с двумя знаками: 300000 тыйын → «3000.00»."""
    return f"{amount_tiyin / 100:.2f}"


def amount_matches(amount_tiyin: int, provider_amount: str) -> bool:
    """Сумма из колбэка совпадает с нашей (провайдер шлёт сомы как число)."""
    try:
        return round(float(provider_amount) * 100) == amount_tiyin
    except ValueError:
        return False
