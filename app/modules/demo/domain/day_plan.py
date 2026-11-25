"""Демо-день: какие приёмы собирает сброс и когда — чистые данные, без БД.

День строится от текущего момента, а не от часов работы: показ идёт «сейчас», и
на экране нужны и прошедшее, и пациент в кресле, и ближайшие записи. Интервалы
не пересекаются даже у одного врача — план ложится и на клинику с единственным
врачом.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.shared.db.enums import AppointmentStatus

#: Начала приёмов кратны этому шагу — «14:35», а не «14:37».
_GRID_MINUTES = 5


class Payment(enum.Enum):
    """Сколько оплачено по приёму демо-дня."""

    NONE = "none"
    PART = "part"  # остаётся долг
    FULL = "full"


@dataclass(frozen=True, slots=True)
class PlannedVisit:
    """Приём демо-дня: смещение от «сейчас» и номер врача по кругу."""

    offset_minutes: int
    duration_minutes: int
    status: AppointmentStatus
    doctor: int
    payment: Payment = Payment.NONE
    #: Есть ли в чеке позиция (у будущих записей и неявки чека ещё нет).
    billed: bool = False


#: Два завершённых (оплаченный и с долгом), неявка час назад, пациент в кресле,
#: три впереди — один подтверждён. Ближайший неподтверждённый начинается меньше
#: чем через час: по нему видна плашка «не подтвердил» и приходит пуш.
DAY_PLAN: tuple[PlannedVisit, ...] = (
    PlannedVisit(-180, 45, AppointmentStatus.COMPLETED, 0, Payment.FULL, billed=True),
    PlannedVisit(-120, 40, AppointmentStatus.COMPLETED, 1, Payment.PART, billed=True),
    PlannedVisit(-60, 30, AppointmentStatus.NO_SHOW, 2),
    PlannedVisit(-20, 40, AppointmentStatus.IN_PROGRESS, 0, billed=True),
    PlannedVisit(40, 30, AppointmentStatus.SCHEDULED, 1),
    PlannedVisit(75, 30, AppointmentStatus.CONFIRMED, 2),
    PlannedVisit(110, 30, AppointmentStatus.SCHEDULED, 0),
)

#: На скольких врачей раскладывается день (сколько есть, но не больше).
MAX_DOCTORS = 3


@dataclass(frozen=True, slots=True)
class DemoPerson:
    first_name: str
    last_name: str
    gender: str
    birth_date: date


#: Выдуманные пациенты — по одному на приём плана, в том же порядке.
PEOPLE: tuple[DemoPerson, ...] = (
    DemoPerson("Айгерим", "Асанова", "female", date(1991, 4, 12)),
    DemoPerson("Бакыт", "Токтогулов", "male", date(1984, 9, 3)),
    DemoPerson("Нурзат", "Сыдыкова", "female", date(1997, 1, 25)),
    DemoPerson("Тимур", "Исаев", "male", date(1979, 11, 8)),
    DemoPerson("Гүлнара", "Осмонова", "female", date(1988, 6, 17)),
    DemoPerson("Данияр", "Абдыкадыров", "male", date(1994, 2, 2)),
    DemoPerson("Жылдыз", "Мамытова", "female", date(2001, 8, 30)),
)


def demo_phone(index: int) -> str:
    """Номер выдуманного пациента: правильный по формату, но ничей.

    Городской номер Бишкека (код 312), который начинается с нуля: ноль —
    префикс выхода на междугородную связь, таких абонентов не бывает. Формат при
    этом проходит проверку телефонов, и карточку можно править в кабинете.
    """
    return f"+9963120{index:05d}"


def anchor(now: datetime) -> datetime:
    """Точка отсчёта дня — «сейчас», округлённое вниз до сетки."""
    return now.replace(minute=now.minute - now.minute % _GRID_MINUTES, second=0, microsecond=0)


def partial_payment(price_tiyin: int) -> int:
    """Половина цены, округлённая вниз до целого сома: остаток — долг."""
    return price_tiyin // 2 // 100 * 100


def is_reset_due(now: datetime, timezone: str | None, reset_at: datetime | None, hour: int) -> bool:
    """Пора ли собрать день по расписанию.

    Только в течение часа ``hour`` по времени клиники: если воркер не работал в
    06:00, догонять сброс в 10:00 посреди показа нельзя. Сделанный в этот же час
    сброс (ручной или прошлым тиком) не повторяется.
    """
    local = now.astimezone(ZoneInfo(timezone or "Asia/Bishkek"))
    if local.hour != hour:
        return False
    due_at = local.replace(minute=0, second=0, microsecond=0)
    return reset_at is None or reset_at < due_at


def visit_starts(plan: PlannedVisit, at: datetime) -> datetime:
    return at + timedelta(minutes=plan.offset_minutes)
