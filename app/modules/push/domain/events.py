"""Каталог доменных событий: адресаты, приоритет, горизонт (ТЗ-СУ §4–§5).

Срочность изменения расписания — функция **горизонта до приёма**, а не факта
изменения: двадцать записей в день иначе дали бы двадцать пушей и отключённые
уведомления к четвергу. Внутренняя шкала — четыре ступени (строки таблицы §5):

    LOW  →  DIGEST (normal с подавлением)  →  NORMAL  →  CRITICAL

«+1» переноса и отмены двигает на одну ступень вверх по этой шкале: отмена за
40 минут — critical (дырка в расписании), отмена за 30 часов — обычный normal
вместо «в сводку». Наружу (в БД и клиенту) DIGEST отдаётся как ``normal`` —
подавление влияет только на выбор канала пуша.

Начавшийся приём событий об изменениях не шлёт — только инбокс (LOW).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.shared.db.enums import EventPriority


class Audience(enum.StrEnum):
    """Кому адресовано событие (колонки каталога §4)."""

    DOCTOR = "doctor"  # врач приёма — только «свои»
    MANAGER = "manager"  # админ/ресепшен = системная роль manager
    OWNER = "owner"


class Rung(enum.IntEnum):
    """Ступень срочности — строки таблицы горизонтов §5."""

    LOW = 0  # только инбокс
    DIGEST = 1  # normal, но пуш копится в ближайшую сводку
    NORMAL = 2  # пуш с задержкой и отменой по показу
    CRITICAL = 3  # пуш немедленно


@dataclass(frozen=True, slots=True)
class EventSpec:
    """Правила одного типа события."""

    audience: frozenset[Audience]
    # None — приоритет считается по горизонту до приёма (буква «Г» каталога).
    static_rung: Rung | None
    # «+1» для переноса и отмены: ступень вверх относительно создания.
    bump: bool = False


_ALL = frozenset({Audience.DOCTOR, Audience.MANAGER, Audience.OWNER})

#: Каталог §4 (та его часть, что уже эмитится; ai.* и digest.* добавятся сами).
EVENT_CATALOG: dict[str, EventSpec] = {
    "appointment.created": EventSpec(_ALL, None),
    "appointment.moved": EventSpec(_ALL, None, bump=True),
    "appointment.canceled": EventSpec(_ALL, None, bump=True),
    "appointment.status_changed": EventSpec(
        frozenset({Audience.DOCTOR, Audience.MANAGER}), Rung.LOW
    ),
    "payment.created": EventSpec(frozenset({Audience.MANAGER, Audience.OWNER}), Rung.LOW),
}


def horizon_rung(scheduled_at: datetime | None, now: datetime) -> Rung:
    """Ступень по времени до приёма (§5); нет даты или приём начался — LOW."""
    if scheduled_at is None:
        return Rung.LOW
    left = scheduled_at - now
    if left <= timedelta(0):
        return Rung.LOW
    if left < timedelta(hours=1):
        return Rung.CRITICAL
    if left < timedelta(hours=4):
        return Rung.NORMAL
    if left < timedelta(hours=48):
        return Rung.DIGEST
    return Rung.LOW


def resolve_rung(spec: EventSpec, scheduled_at: datetime | None, now: datetime) -> Rung:
    """Итоговая ступень: статическая либо по горизонту, с учётом «+1»."""
    rung = spec.static_rung if spec.static_rung is not None else horizon_rung(scheduled_at, now)
    if spec.bump:
        rung = Rung(min(int(rung) + 1, int(Rung.CRITICAL)))
    return rung


def stored_priority(rung: Rung) -> EventPriority:
    """Ступень → хранимый приоритет: DIGEST наружу неотличим от normal."""
    if rung is Rung.CRITICAL:
        return EventPriority.CRITICAL
    if rung in (Rung.NORMAL, Rung.DIGEST):
        return EventPriority.NORMAL
    return EventPriority.LOW


def format_som(amount_tiyin: int) -> str:
    """Сумма в тыйынах → строка в сомах для текста уведомления.

    Целые сомы — без дробной части («2 500»), иначе с копейками («637,50»):
    десятичная запятая привычнее и ru, и kg.
    """
    soms, tiyin = divmod(abs(amount_tiyin), 100)
    sign = "-" if amount_tiyin < 0 else ""
    base = f"{soms:,}".replace(",", " ")
    return f"{sign}{base}" if tiyin == 0 else f"{sign}{base},{tiyin:02d}"
