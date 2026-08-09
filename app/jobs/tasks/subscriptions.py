"""Задачи по расписанию: состояние подписок клиник.

⚠️ Доступ эти задачи **не закрывают**. И конец триала, и конец оплаченного
периода считает политика подписки по датам — упавший воркер не должен раздавать
бесплатный доступ. Задачи приводят статус в БД в соответствие с реальностью
(чтобы админка, дашборд и отчёты не врали) и предупреждают людей.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.db import session_factory
from app.jobs.tasks.context import attempt, notifier
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.modules.subscriptions.application.use_cases.expire_paid_periods import (
    ExpirePaidPeriodsUseCase,
)
from app.modules.subscriptions.application.use_cases.expire_trials import ExpireTrialsUseCase
from app.modules.subscriptions.application.use_cases.warn_trial_ending import (
    WarnTrialEndingUseCase,
)
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)


async def expire_trials(ctx: dict[str, Any]) -> int:
    """Перевести истёкшие триалы в ``trial_expired`` и сообщить об этом."""
    async with JobRunRecorder("expire_trials", attempt(ctx)) as run:
        async with session_factory() as session:
            use_case = ExpireTrialsUseCase(
                session, SubscriptionRepository(session), notifier(ctx)
            )
            count = await use_case.execute()
        run.note(f"переведено клиник: {count}")
        return count


async def expire_paid_periods(ctx: dict[str, Any]) -> int:
    """Перевести клиники с истёкшим оплаченным периодом в ``past_due``."""
    async with JobRunRecorder("expire_paid_periods", attempt(ctx)) as run:
        async with session_factory() as session:
            use_case = ExpirePaidPeriodsUseCase(
                session, SubscriptionRepository(session), notifier(ctx)
            )
            count = await use_case.execute()
        run.note(f"переведено клиник: {count}")
        return count


async def warn_trial_ending(ctx: dict[str, Any]) -> int:
    """Предупредить владельцев, у кого триал кончается в ближайшие дни."""
    async with JobRunRecorder("warn_trial_ending", attempt(ctx)) as run:
        async with session_factory() as session:
            use_case = WarnTrialEndingUseCase(
                session, SubscriptionRepository(session), notifier(ctx)
            )
            count = await use_case.execute(settings.trial_warning_days)
        run.note(f"предупреждено клиник: {count}")
        return count
