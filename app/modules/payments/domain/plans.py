"""Смена тарифа клиникой: правила перехода между пакетами и доплата.

Пакет клиники — это её лимит мест: «Практика» значит ``seats_limit = 6``.
Переход делается самой клиникой и только между публичными пакетами; мест
больше последнего пакета — разговор со Skrezo и спецплан.

* **Дороже** — сразу. Если идёт оплаченный период, разница цен за его остаток
  списывается с баланса: по дням, месяц считается за 30 дней, округление в
  пользу копейки вверх. На триале и без оплаченного периода — без доплаты,
  новая цена — со следующей оплаты.
* **Дешевле** — сразу и без возврата: новая цена — со следующей оплаты. Только
  если сотрудников не больше мест нового пакета.
"""

from __future__ import annotations

from datetime import datetime

#: Месяц для доплаты за остаток периода — 30 дней.
DAYS_IN_MONTH = 30
_DAY_SECONDS = 86400

#: Почему клиника не может сменить пакет сама.
BLOCK_SOLO = "solo"
BLOCK_CUSTOM_PLAN = "custom_plan_active"
BLOCK_SUBSCRIPTION_BLOCKED = "subscription_blocked"
BLOCK_SUBSCRIPTION_CANCELLED = "subscription_cancelled"

#: Почему недоступен конкретный пакет.
UNAVAILABLE_CURRENT = "current"
UNAVAILABLE_SEATS_IN_USE = "seats_in_use"


def remaining_days(now: datetime, period_end: datetime | None) -> int:
    """Дней до конца оплаченного периода, неполный день — целый; 0 — периода нет."""
    if period_end is None or period_end <= now:
        return 0
    seconds = (period_end - now).total_seconds()
    return int(-(-seconds // _DAY_SECONDS))


def upgrade_charge(monthly_diff_tiyin: int, days: int) -> int:
    """Доплата за ``days`` дней при росте цены на ``monthly_diff_tiyin`` в месяц.

    Целые тыйыны, округление вверх: 1 333,33… сома за 8 дней — 1 333,34.
    Пакет не дороже или дней не осталось — 0.
    """
    if monthly_diff_tiyin <= 0 or days <= 0:
        return 0
    return -(-monthly_diff_tiyin * days // DAYS_IN_MONTH)
