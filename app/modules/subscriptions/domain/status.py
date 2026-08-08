"""Срок действия подписки: до какой даты и сколько дней осталось.

Клинике нужен ответ на один вопрос — «до какого числа я работаю и сколько это
дней». Даты у триала и у оплаченного периода лежат в разных колонках, поэтому
выбор колонки и арифметика собраны здесь, а не размазаны по схемам ответа.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.modules.subscriptions.domain.access import ClinicAccess, clinic_access
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.subscription import Subscription


@dataclass(frozen=True, slots=True)
class SubscriptionView:
    """Подписка вместе с посчитанным сроком действия."""

    subscription: Subscription
    #: До какой даты действует доступ (триал → конец триала, иначе конец периода).
    expires_at: datetime | None
    #: Целых дней до этой даты; 0 — идёт последний день либо срок уже вышел.
    days_left: int
    #: Срок вышел (у оплаченной подписки без периода — False: даты нет).
    expired: bool
    #: Клиника в режиме чтения — мутирующие операции закрыты.
    writes_blocked: bool


def expires_at(subscription: Subscription) -> datetime | None:
    """Дата окончания текущего срока: триал — свой, остальные — оплаченный период."""
    if subscription.status is SubscriptionStatus.TRIAL:
        return subscription.trial_ends_at
    return subscription.current_period_end


def days_left(deadline: datetime | None, now: datetime) -> int:
    """Целых суток до ``deadline``; отрицательное и отсутствующее — ноль."""
    if deadline is None:
        return 0
    return max(0, (deadline - now).days)


def build_view(subscription: Subscription, now: datetime) -> SubscriptionView:
    """Собрать представление подписки на момент ``now``."""
    deadline = expires_at(subscription)
    access = clinic_access(subscription.status, subscription.trial_ends_at, now)
    return SubscriptionView(
        subscription=subscription,
        expires_at=deadline,
        days_left=days_left(deadline, now),
        expired=deadline is not None and deadline <= now,
        writes_blocked=access is ClinicAccess.READ_ONLY,
    )
