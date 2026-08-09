"""Зависимости модуля подписки."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.access.application.services.subscription_policy import SubscriptionPolicy
from app.modules.access.presentation.deps import get_subscription_policy
from app.modules.subscriptions.application.use_cases.get_subscription import (
    GetSubscriptionUseCase,
)
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)


def get_subscription_repository(
    session: AsyncSession = Depends(get_session),
) -> SubscriptionRepository:
    return SubscriptionRepository(session)


def get_get_subscription_use_case(
    subscriptions: SubscriptionRepository = Depends(get_subscription_repository),
) -> GetSubscriptionUseCase:
    return GetSubscriptionUseCase(subscriptions)


def get_manage_subscription_use_case(
    session: AsyncSession = Depends(get_session),
    subscriptions: SubscriptionRepository = Depends(get_subscription_repository),
    policy: SubscriptionPolicy = Depends(get_subscription_policy),
) -> ManageSubscriptionUseCase:
    return ManageSubscriptionUseCase(session, subscriptions, policy)
