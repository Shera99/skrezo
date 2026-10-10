"""Сборка зависимостей клиентского контура (repository → service → use-case)."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.redis import get_redis
from app.modules.access.application.services.permission_resolver import PermissionResolver
from app.modules.access.application.services.subscription_policy import (
    SubscriptionPolicy,
)
from app.modules.access.presentation.deps import get_permission_resolver, get_subscription_policy
from app.modules.clinics.application.services.provisioning_service import build_provisioning_service
from app.modules.identity.application.services.client_token_service import ClientTokenService
from app.modules.identity.application.services.otp_delivery_service import OtpDeliveryService
from app.modules.identity.application.services.otp_service import OtpService
from app.modules.identity.application.use_cases.change_password import ChangePasswordUseCase
from app.modules.identity.application.use_cases.complete_onboarding import (
    CompleteOnboardingUseCase,
)
from app.modules.identity.application.use_cases.get_me import GetMeUseCase
from app.modules.identity.application.use_cases.login import LoginUseCase
from app.modules.identity.application.use_cases.logout import LogoutUseCase
from app.modules.identity.application.use_cases.refresh_token import RefreshTokenUseCase
from app.modules.identity.application.use_cases.register_solo import RegisterSoloUseCase
from app.modules.identity.application.use_cases.request_otp import RequestOtpUseCase
from app.modules.identity.application.use_cases.reset_password import ResetPasswordUseCase
from app.modules.identity.application.use_cases.select_clinic import SelectClinicUseCase
from app.modules.identity.application.use_cases.update_profile import UpdateProfileUseCase
from app.modules.identity.application.use_cases.verify_otp import VerifyOtpUseCase
from app.modules.identity.infrastructure.otp.channels import OtpChannel
from app.modules.identity.infrastructure.otp.factory import build_channels
from app.modules.identity.infrastructure.repositories.account_repository import AccountRepository
from app.modules.identity.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)


def get_account_repository(session: AsyncSession = Depends(get_session)) -> AccountRepository:
    return AccountRepository(session)


def get_membership_repository(session: AsyncSession = Depends(get_session)) -> MembershipRepository:
    return MembershipRepository(session)


def get_client_token_service(redis: Redis = Depends(get_redis)) -> ClientTokenService:
    return ClientTokenService(redis)


def get_login_use_case(
    accounts: AccountRepository = Depends(get_account_repository),
    memberships: MembershipRepository = Depends(get_membership_repository),
    tokens: ClientTokenService = Depends(get_client_token_service),
) -> LoginUseCase:
    return LoginUseCase(accounts, memberships, tokens)


@lru_cache
def _otp_channels() -> tuple[OtpChannel, ...]:
    """Каналы доставки — один раз на процесс.

    Сборка читает настройки и пишет предупреждения о ненастроенных провайдерах:
    делать это на каждый запрос значило бы засорять лог и заново решать то, что
    от запроса не зависит.
    """
    return tuple(build_channels())


def get_otp_service(redis: Redis = Depends(get_redis)) -> OtpService:
    return OtpService(redis, OtpDeliveryService(list(_otp_channels())))


def get_request_otp_use_case(otp: OtpService = Depends(get_otp_service)) -> RequestOtpUseCase:
    return RequestOtpUseCase(otp)


def get_verify_otp_use_case(
    otp: OtpService = Depends(get_otp_service),
    accounts: AccountRepository = Depends(get_account_repository),
    memberships: MembershipRepository = Depends(get_membership_repository),
    tokens: ClientTokenService = Depends(get_client_token_service),
) -> VerifyOtpUseCase:
    return VerifyOtpUseCase(otp, accounts, memberships, tokens)


def get_register_solo_use_case(
    session: AsyncSession = Depends(get_session),
    otp: OtpService = Depends(get_otp_service),
    accounts: AccountRepository = Depends(get_account_repository),
    memberships: MembershipRepository = Depends(get_membership_repository),
    tokens: ClientTokenService = Depends(get_client_token_service),
) -> RegisterSoloUseCase:
    # Провижининг делит ту же сессию → создание соло атомарно.
    return RegisterSoloUseCase(
        otp, build_provisioning_service(session), accounts, memberships, tokens
    )


def get_change_password_use_case(
    session: AsyncSession = Depends(get_session),
    accounts: AccountRepository = Depends(get_account_repository),
) -> ChangePasswordUseCase:
    return ChangePasswordUseCase(session, accounts)


def get_reset_password_use_case(
    session: AsyncSession = Depends(get_session),
    otp: OtpService = Depends(get_otp_service),
    accounts: AccountRepository = Depends(get_account_repository),
    tokens: ClientTokenService = Depends(get_client_token_service),
) -> ResetPasswordUseCase:
    return ResetPasswordUseCase(session, otp, accounts, tokens)


def get_refresh_token_use_case(
    tokens: ClientTokenService = Depends(get_client_token_service),
) -> RefreshTokenUseCase:
    return RefreshTokenUseCase(tokens)


def get_logout_use_case(
    tokens: ClientTokenService = Depends(get_client_token_service),
) -> LogoutUseCase:
    return LogoutUseCase(tokens)


def get_select_clinic_use_case(
    memberships: MembershipRepository = Depends(get_membership_repository),
    tokens: ClientTokenService = Depends(get_client_token_service),
) -> SelectClinicUseCase:
    return SelectClinicUseCase(memberships, tokens)


def get_get_me_use_case(
    accounts: AccountRepository = Depends(get_account_repository),
    memberships: MembershipRepository = Depends(get_membership_repository),
    resolver: PermissionResolver = Depends(get_permission_resolver),
    policy: SubscriptionPolicy = Depends(get_subscription_policy),
) -> GetMeUseCase:
    return GetMeUseCase(accounts, memberships, resolver, policy)


def get_update_profile_use_case(
    session: AsyncSession = Depends(get_session),
    accounts: AccountRepository = Depends(get_account_repository),
) -> UpdateProfileUseCase:
    return UpdateProfileUseCase(session, accounts)


def get_complete_onboarding_use_case(
    session: AsyncSession = Depends(get_session),
    accounts: AccountRepository = Depends(get_account_repository),
) -> CompleteOnboardingUseCase:
    return CompleteOnboardingUseCase(session, accounts)
