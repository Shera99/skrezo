"""Разрешённые переходы между статусами приёма.

Воронка дня: ``draft → scheduled → confirmed → arrived → in_progress →
completed``; из рабочих состояний приём можно отменить (``cancelled``) или
отметить неявку (``no_show``).

Переходы делятся на два класса:

* **вперёд по воронке** — обычная работа регистратора, ничего не требует
  (в том числе «перепрыгнуть»: записали и сразу закрыли приём);
* **откат назад** — узкий и подотчётный: ровно один шаг назад, отдельное право
  ``appointments.status_rollback``, только для приёма текущего дня, обязательный
  комментарий и запись в аудит. Без отката промах кнопкой не исправить, и
  администратор начнёт плодить дубли — но и разрешать его свободно нельзя.

``completed`` — терминальный: за завершённым приёмом стоят платежи кассы и
выручка врача. Ошибочно закрытый приём исправляется **возвратом платежа**, а не
переписыванием статуса, иначе отчётам нельзя доверять.
"""

from __future__ import annotations

import enum

from app.shared.db.enums import AppointmentStatus

_CLOSING = frozenset(
    {AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW}
)

# Движение вперёд: дальше по воронке либо в закрывающий статус.
FORWARD_TRANSITIONS: dict[AppointmentStatus, frozenset[AppointmentStatus]] = {
    # Черновик доводится до записи либо закрывается отменой.
    AppointmentStatus.DRAFT: frozenset({AppointmentStatus.SCHEDULED, AppointmentStatus.CANCELLED}),
    AppointmentStatus.SCHEDULED: frozenset(
        {AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED, AppointmentStatus.IN_PROGRESS}
    )
    | _CLOSING,
    AppointmentStatus.CONFIRMED: frozenset(
        {AppointmentStatus.ARRIVED, AppointmentStatus.IN_PROGRESS}
    )
    | _CLOSING,
    AppointmentStatus.ARRIVED: frozenset({AppointmentStatus.IN_PROGRESS}) | _CLOSING,
    # Прервать начатый приём можно — с обязательной причиной отмены.
    AppointmentStatus.IN_PROGRESS: frozenset(
        {AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED}
    ),
    AppointmentStatus.COMPLETED: frozenset(),
    AppointmentStatus.CANCELLED: frozenset(),
    AppointmentStatus.NO_SHOW: frozenset(),
}

# Откат: ровно один шаг назад по воронке (плюс возврат из неявки и отмены).
ROLLBACK_TRANSITIONS: dict[AppointmentStatus, frozenset[AppointmentStatus]] = {
    AppointmentStatus.CONFIRMED: frozenset({AppointmentStatus.SCHEDULED}),
    AppointmentStatus.ARRIVED: frozenset({AppointmentStatus.CONFIRMED}),
    AppointmentStatus.IN_PROGRESS: frozenset({AppointmentStatus.ARRIVED}),
    # Опоздал, но всё-таки пришёл — ежедневный случай.
    AppointmentStatus.NO_SHOW: frozenset(
        {AppointmentStatus.SCHEDULED, AppointmentStatus.ARRIVED}
    ),
    # Отмену снимают только в исходное состояние — и лишь если слот свободен.
    AppointmentStatus.CANCELLED: frozenset({AppointmentStatus.SCHEDULED}),
}


class TransitionKind(enum.StrEnum):
    """Чем является смена статуса: ничем, шагом вперёд, откатом или запретом."""

    SAME = "same"
    FORWARD = "forward"
    ROLLBACK = "rollback"
    FORBIDDEN = "forbidden"


def classify_transition(
    current: AppointmentStatus, target: AppointmentStatus
) -> TransitionKind:
    """Определить класс перехода ``current → target``."""
    if current is target:
        return TransitionKind.SAME
    if target in FORWARD_TRANSITIONS.get(current, frozenset()):
        return TransitionKind.FORWARD
    if target in ROLLBACK_TRANSITIONS.get(current, frozenset()):
        return TransitionKind.ROLLBACK
    return TransitionKind.FORBIDDEN
