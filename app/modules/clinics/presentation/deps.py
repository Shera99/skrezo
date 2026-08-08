"""Зависимости провижининга: сборка сервиса из одной сессии (одна транзакция)."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.access.application.services.role_provisioning_service import (
    RoleProvisioningService,
)
from app.modules.access.infrastructure.repositories.role_provisioning_repository import (
    RoleProvisioningRepository,
)
from app.modules.clinics.application.services.admin_notification_service import (
    AdminNotificationService,
)
from app.modules.clinics.application.services.provisioning_service import ProvisioningService
from app.modules.clinics.application.use_cases.clinic_settings import (
    GetClinicSettingsUseCase,
    UpdateClinicSettingsUseCase,
)
from app.modules.clinics.application.use_cases.create_clinic import CreateClinicUseCase
from app.modules.clinics.infrastructure.repositories.account_repository import AccountRepository
from app.modules.clinics.infrastructure.repositories.clinic_repository import ClinicRepository
from app.modules.clinics.infrastructure.repositories.clinic_settings_repository import (
    ClinicSettingsRepository,
)
from app.modules.clinics.infrastructure.repositories.membership_repository import (
    MembershipRepository,
)
from app.modules.leads.infrastructure.repositories.lead_repository import LeadRepository
from app.modules.subscriptions.application.services.subscription_service import SubscriptionService
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)


def get_provisioning_service(
    session: AsyncSession = Depends(get_session),
) -> ProvisioningService:
    # Все репозитории делят одну сессию → провижининг атомарен.
    return ProvisioningService(
        session=session,
        clinics=ClinicRepository(session),
        accounts=AccountRepository(session),
        memberships=MembershipRepository(session),
        roles=RoleProvisioningService(RoleProvisioningRepository(session)),
        subscriptions=SubscriptionService(SubscriptionRepository(session)),
        leads=LeadRepository(session),
    )


def get_admin_notification_service() -> AdminNotificationService:
    return AdminNotificationService()


def get_create_clinic_use_case(
    provisioning: ProvisioningService = Depends(get_provisioning_service),
    notifier: AdminNotificationService = Depends(get_admin_notification_service),
) -> CreateClinicUseCase:
    return CreateClinicUseCase(provisioning, notifier)


def get_clinic_settings_repository(
    session: AsyncSession = Depends(get_session),
) -> ClinicSettingsRepository:
    return ClinicSettingsRepository(session)


def get_get_clinic_settings_use_case(
    clinics: ClinicSettingsRepository = Depends(get_clinic_settings_repository),
) -> GetClinicSettingsUseCase:
    return GetClinicSettingsUseCase(clinics)


def get_update_clinic_settings_use_case(
    session: AsyncSession = Depends(get_session),
    clinics: ClinicSettingsRepository = Depends(get_clinic_settings_repository),
) -> UpdateClinicSettingsUseCase:
    return UpdateClinicSettingsUseCase(session, clinics)
