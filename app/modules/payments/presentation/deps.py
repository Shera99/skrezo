"""Зависимости платежей: репозиторий, провайдер, журнал, use-case'ы."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.payments.application.services.ledger_service import LedgerService
from app.modules.payments.application.services.pricing import SubscriptionPricing
from app.modules.payments.application.use_cases.change_package import ChangePackageUseCase
from app.modules.payments.application.use_cases.custom_plan import (
    RemoveCustomPlanUseCase,
    SetCustomPlanUseCase,
)
from app.modules.payments.application.use_cases.handle_freedompay_result import (
    HandleFreedomPayResultUseCase,
)
from app.modules.payments.application.use_cases.plan_options import GetPlanOptionsUseCase
from app.modules.payments.application.use_cases.plan_requests import (
    ListPlanRequestsUseCase,
    ResolvePlanRequestUseCase,
    SubmitPlanRequestUseCase,
)
from app.modules.payments.application.use_cases.start_subscription_payment import (
    StartSubscriptionPaymentUseCase,
)
from app.modules.payments.infrastructure.repositories.platform_payment_repository import (
    PlatformPaymentRepository,
)
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.modules.subscriptions.presentation.deps import get_manage_subscription_use_case
from app.shared.freedompay import FreedomPayClient, build_freedompay_client
from app.shared.notifications.deps import get_notifier
from app.shared.notifications.notifier import Notifier


def get_payment_repository(
    session: AsyncSession = Depends(get_session),
) -> PlatformPaymentRepository:
    return PlatformPaymentRepository(session)


def get_ledger_service(session: AsyncSession = Depends(get_session)) -> LedgerService:
    """Журнал операций и кошелёк клиники."""
    return LedgerService(session)


def get_pricing(session: AsyncSession = Depends(get_session)) -> SubscriptionPricing:
    return SubscriptionPricing(session)


def get_freedompay_client() -> FreedomPayClient | None:
    """Провайдер по настройкам; ``None`` — онлайн-оплата не подключена."""
    return build_freedompay_client()


def get_start_payment_use_case(
    session: AsyncSession = Depends(get_session),
    payments: PlatformPaymentRepository = Depends(get_payment_repository),
    pricing: SubscriptionPricing = Depends(get_pricing),
    provider: FreedomPayClient | None = Depends(get_freedompay_client),
) -> StartSubscriptionPaymentUseCase:
    return StartSubscriptionPaymentUseCase(session, payments, pricing, provider)


def get_result_use_case(
    session: AsyncSession = Depends(get_session),
    payments: PlatformPaymentRepository = Depends(get_payment_repository),
    ledger: LedgerService = Depends(get_ledger_service),
    manage: ManageSubscriptionUseCase = Depends(get_manage_subscription_use_case),
) -> HandleFreedomPayResultUseCase:
    return HandleFreedomPayResultUseCase(session, payments, ledger, manage)


# ── Смена тарифа и спецплан ────────────────────────────────────────────────


def get_plan_options_use_case(
    session: AsyncSession = Depends(get_session),
) -> GetPlanOptionsUseCase:
    return GetPlanOptionsUseCase(session)


def get_change_package_use_case(
    session: AsyncSession = Depends(get_session),
    notifier: Notifier = Depends(get_notifier),
) -> ChangePackageUseCase:
    return ChangePackageUseCase(session, notifier)


def get_submit_plan_request_use_case(
    session: AsyncSession = Depends(get_session),
    notifier: Notifier = Depends(get_notifier),
) -> SubmitPlanRequestUseCase:
    return SubmitPlanRequestUseCase(session, notifier)


def get_set_custom_plan_use_case(
    session: AsyncSession = Depends(get_session),
) -> SetCustomPlanUseCase:
    return SetCustomPlanUseCase(session)


def get_remove_custom_plan_use_case(
    session: AsyncSession = Depends(get_session),
) -> RemoveCustomPlanUseCase:
    return RemoveCustomPlanUseCase(session)


def get_list_plan_requests_use_case(
    session: AsyncSession = Depends(get_session),
) -> ListPlanRequestsUseCase:
    return ListPlanRequestsUseCase(session)


def get_resolve_plan_request_use_case(
    session: AsyncSession = Depends(get_session),
) -> ResolvePlanRequestUseCase:
    return ResolvePlanRequestUseCase(session)
