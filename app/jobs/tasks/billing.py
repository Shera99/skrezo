"""Автопродление подписок с баланса — раз в сутки утром.

03:30 UTC = 09:30 по Бишкеку: владелец получает предупреждение о нехватке
баланса в рабочее время, а продление проходит до начала приёма.
"""

from __future__ import annotations

from typing import Any

from app.core.db import session_factory
from app.jobs.tasks.context import attempt, notifier
from app.modules.access.application.services.subscription_policy import SubscriptionPolicy
from app.modules.access.infrastructure.repositories.subscription_policy_repository import (
    SubscriptionPolicyRepository,
)
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.modules.payments.application.services.ledger_service import LedgerService
from app.modules.payments.application.use_cases.autopay import AutopaySubscriptionsUseCase
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)


async def autopay_subscriptions(ctx: dict[str, Any]) -> str:
    """Продлить с баланса, кого пора; предупредить, кому не хватает; вернуть сводку."""
    async with JobRunRecorder("autopay_subscriptions", attempt(ctx)) as run:
        async with session_factory() as session:
            manage = ManageSubscriptionUseCase(
                session,
                SubscriptionRepository(session),
                SubscriptionPolicy(SubscriptionPolicyRepository(session), ctx["redis"]),
            )
            report = await AutopaySubscriptionsUseCase(
                session, LedgerService(session), manage, notifier(ctx)
            ).execute()
        summary = f"продлено: {len(report.renewed)}, предупреждено: {len(report.warned)}"
        run.note(summary)
        return summary
