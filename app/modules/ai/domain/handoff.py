"""Передача диалога человеку: когда клиника снова на связи — чистая арифметика.

Пациенту, которого бот передал администратору ночью или в выходной, мало
«администратор ответит»: он ждёт ответа сейчас. Шаблон передачи добавляет,
когда клиника откроется (переписка 13.10.2026, решение 15.10.2026).

Обед закрытием не считается: клиника работает, администратор в здании.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from app.modules.schedule.domain.models import WorkingHours

#: Сколько дней вперёд искать рабочий день: неделя и запас на сегодняшний.
_LOOKAHEAD_DAYS = 8


def next_opening(hours_for: Callable[[int], WorkingHours], now: datetime) -> datetime | None:
    """Ближайшее открытие клиники во времени ``now`` или ``None``, если открыта сейчас.

    ``hours_for(weekday)`` — график дня недели (0 — понедельник). ``now`` —
    aware-время клиники. Ни одного рабочего дня в графике — тоже ``None``:
    утверждать «клиника закрыта» без графика нельзя.
    """
    for offset in range(_LOOKAHEAD_DAYS):
        day = now.date() + timedelta(days=offset)
        hours = hours_for(day.weekday())
        if not hours.is_working:
            continue
        opens = datetime.combine(day, hours.opens_at, tzinfo=now.tzinfo)
        closes = datetime.combine(day, hours.closes_at, tzinfo=now.tzinfo)
        if offset == 0 and opens <= now < closes:
            return None
        if opens > now:
            return opens
    return None
