"""Когда продлевать с баланса: действующий период и идущий триал — в последние дни."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.modules.payments.application.services.ledger_service import subscription_due
from app.modules.payments.domain.periods import add_months, next_period, paid_through
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.subscription import Subscription

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


def _sub(
    status: SubscriptionStatus, *, trial_in: timedelta, period_in: timedelta | None = None
) -> Subscription:
    return Subscription(
        status=status,
        trial_started_at=NOW - timedelta(days=10),
        trial_ends_at=NOW + trial_in,
        current_period_end=NOW + period_in if period_in is not None else None,
    )


def test_trial_is_due_only_in_its_last_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "autopay_days_before", 3)
    trial = SubscriptionStatus.TRIAL
    assert not subscription_due(_sub(trial, trial_in=timedelta(days=20)), NOW)
    assert subscription_due(_sub(trial, trial_in=timedelta(days=2)), NOW)


def test_active_and_closed_statuses_keep_their_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "autopay_days_before", 3)
    week = timedelta(days=7)
    active = SubscriptionStatus.ACTIVE
    assert not subscription_due(_sub(active, trial_in=-week, period_in=timedelta(days=20)), NOW)
    assert subscription_due(_sub(active, trial_in=-week, period_in=timedelta(days=1)), NOW)
    assert subscription_due(_sub(SubscriptionStatus.TRIAL_EXPIRED, trial_in=-week), NOW)
    assert subscription_due(_sub(SubscriptionStatus.PAST_DUE, trial_in=-week), NOW)


def test_new_period_starts_where_access_ends() -> None:
    trial_end = NOW + timedelta(days=12)
    through = paid_through(
        SubscriptionStatus.TRIAL, trial_ends_at=trial_end, current_period_end=None
    )
    assert next_period(NOW, through, 1) == (trial_end, add_months(trial_end, 1))
    # Истёкший триал доступа не даёт — период с момента оплаты.
    expired = paid_through(
        SubscriptionStatus.TRIAL_EXPIRED,
        trial_ends_at=NOW - timedelta(days=1),
        current_period_end=None,
    )
    assert expired is None and next_period(NOW, expired, 1)[0] == NOW
