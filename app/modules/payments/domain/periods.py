"""Оплаченный период: продление на N месяцев и деньги провайдера."""

from __future__ import annotations

import calendar
from datetime import datetime

from app.shared.db.enums import SubscriptionStatus

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


def paid_through(
    status: SubscriptionStatus,
    *,
    trial_ends_at: datetime | None,
    current_period_end: datetime | None,
) -> datetime | None:
    """До какого момента у клиники уже есть доступ — от него считается новый период.

    Действующий оплаченный период — до его конца, идущий пробный — до конца
    триала: оплата во время триала не сжигает оставшиеся дни, как у
    большинства сервисов с пробным периодом (решение владельца от 26.09.2026).
    Остальные статусы доступа не дают — новый период начнётся с момента оплаты.
    Прошла ли дата, решает :func:`next_period`.
    """
    if status is SubscriptionStatus.ACTIVE:
        return current_period_end
    if status is SubscriptionStatus.TRIAL:
        return trial_ends_at
    return None


def som_amount(amount_tiyin: int) -> str:
    """Сумма провайдеру — сомы с двумя знаками: 300000 тыйын → «3000.00»."""
    return f"{amount_tiyin / 100:.2f}"


def amount_matches(amount_tiyin: int, provider_amount: str) -> bool:
    """Сумма из колбэка совпадает с нашей (провайдер шлёт сомы как число)."""
    try:
        return round(float(provider_amount) * 100) == amount_tiyin
    except ValueError:
        return False
