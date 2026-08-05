"""Разрешённые переходы между статусами приёма.

Жизненный цикл: ``draft → scheduled → confirmed → arrived → in_progress →
completed``; из любого рабочего состояния приём можно отменить (``cancelled``)
или отметить неявку (``no_show``).

Правила намеренно **свободные внутри «активных» статусов** и **строгие там, где
появляются деньги**:

* между активными (``scheduled``/``confirmed``/``arrived``/``in_progress``)
  двигаться можно в любую сторону: регистратор ошибся кнопкой — поправит, денег
  это не касается;
* ``completed`` — терминальный: по завершённому приёму уже проведены платежи в
  кассе, и «расзавершение» рассинхронизировало бы кассу, долги и отчёты;
* отмену и неявку можно снять (приём вернётся в работу) — при этом слот
  перепроверяется на занятость, как и при обычном переносе;
* вернуть приём в ``draft`` нельзя: у черновика пустая дата, а у рабочего
  приёма она уже есть (и NULL в неё не пускает защита из миграции 0020).
"""

from __future__ import annotations

from app.shared.db.enums import ACTIVE_APPOINTMENT_STATUSES, AppointmentStatus

_ACTIVE = ACTIVE_APPOINTMENT_STATUSES
_CLOSED = frozenset({AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED})

# Куда можно перейти из каждого статуса (переход «в себя» разрешён всегда).
ALLOWED_TRANSITIONS: dict[AppointmentStatus, frozenset[AppointmentStatus]] = {
    # Черновик либо доводится до записи, либо отменяется.
    AppointmentStatus.DRAFT: frozenset({AppointmentStatus.SCHEDULED, AppointmentStatus.CANCELLED}),
    AppointmentStatus.SCHEDULED: _ACTIVE | _CLOSED | {AppointmentStatus.NO_SHOW},
    AppointmentStatus.CONFIRMED: _ACTIVE | _CLOSED | {AppointmentStatus.NO_SHOW},
    AppointmentStatus.ARRIVED: _ACTIVE | _CLOSED | {AppointmentStatus.NO_SHOW},
    AppointmentStatus.IN_PROGRESS: _ACTIVE | _CLOSED | {AppointmentStatus.NO_SHOW},
    # Снятие отмены/неявки: приём возвращается в работу или сразу закрывается.
    AppointmentStatus.CANCELLED: _ACTIVE | {AppointmentStatus.COMPLETED},
    AppointmentStatus.NO_SHOW: _ACTIVE | _CLOSED,
    # Завершённый приём не меняет статус: за ним стоят платежи кассы.
    AppointmentStatus.COMPLETED: frozenset(),
}


def is_transition_allowed(current: AppointmentStatus, target: AppointmentStatus) -> bool:
    """Можно ли перевести приём из ``current`` в ``target``."""
    if current is target:
        return True
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())
