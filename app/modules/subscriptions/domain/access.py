"""Что клинике позволено при её статусе подписки.

Правило считается **по данным подписки, а не по факту прогона джобы**: триал
кончается ровно в ``trial_ends_at``, даже если воркер лежит. Иначе упавший
воркер означал бы бесплатный доступ — а это ровно тот случай, когда ошибка
инфраструктуры молча превращается в потерю денег.

Закрытая подписка не выключает клинику целиком: чтение остаётся. Клиника
видит своих пациентов, расписание и историю, но не заводит новые приёмы и не
проводит платежи. Полное отключение оставило бы её без данных в разгар дня, а
оплатить в этот момент всё равно нечем — биллинг отложен.
"""

from __future__ import annotations

import enum
from datetime import datetime

from app.shared.db.enums import SubscriptionStatus


class ClinicAccess(enum.StrEnum):
    """Уровень доступа клиники при текущем состоянии подписки."""

    FULL = "full"
    READ_ONLY = "read_only"


def clinic_access(
    status: SubscriptionStatus, trial_ends_at: datetime | None, now: datetime
) -> ClinicAccess:
    """Полный доступ или только чтение.

    Полный — у оплаченной подписки и у **не истёкшего** триала. Всё остальное
    (истёкший триал, ожидание карты, просрочка платежа, ручной блок, уход
    клиента) — только чтение.
    """
    if status is SubscriptionStatus.ACTIVE:
        return ClinicAccess.FULL
    if status is SubscriptionStatus.TRIAL:
        expired = trial_ends_at is not None and trial_ends_at <= now
        return ClinicAccess.READ_ONLY if expired else ClinicAccess.FULL
    return ClinicAccess.READ_ONLY
