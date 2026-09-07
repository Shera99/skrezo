"""Тихие часы — чистая арифметика, без БД (ТЗ-СУ §13).

Дефолт — по графику работы клиники: вне рабочего окна (до открытия, после
закрытия, в выходной) normal-пуши не уходят, а копятся до утренней сводки;
бейдж в каждом пуше и инбокс доносят накопленное. ``critical`` проходит
всегда — эти проверки к нему просто не применяются. Персональные тихие часы
(``notification_pref.quiet_from/to``) работают ПОВЕРХ клинических: сотрудник
может расширить тишину, но не отменить сводку.

Обед тихими часами не считается: клиника работает, персонал в здании.
"""

from __future__ import annotations

from datetime import time


def clinic_quiet(*, is_working: bool, opens_at: time, closes_at: time, local_time: time) -> bool:
    """Тихо ли сейчас по графику клиники (выходной — тихо весь день)."""
    if not is_working:
        return True
    return local_time < opens_at or local_time >= closes_at


def personal_quiet(quiet_from: time | None, quiet_to: time | None, local_time: time) -> bool:
    """Тихо ли по персональным настройкам; интервал может переходить полночь."""
    if quiet_from is None or quiet_to is None:
        return False
    if quiet_from <= quiet_to:
        return quiet_from <= local_time < quiet_to
    # 22:00–07:00: тишина «через полночь».
    return local_time >= quiet_from or local_time < quiet_to
