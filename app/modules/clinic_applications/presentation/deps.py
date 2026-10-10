"""Зависимости модуля заявок клиник."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.clinic_applications.application.services.team_notification_service import (
    TeamNotificationService,
)
from app.modules.clinic_applications.application.use_cases.get_application import (
    GetClinicApplicationUseCase,
)
from app.modules.clinic_applications.application.use_cases.list_applications import (
    ListClinicApplicationsUseCase,
)
from app.modules.clinic_applications.application.use_cases.set_application_status import (
    SetApplicationStatusUseCase,
)
from app.modules.clinic_applications.application.use_cases.submit_application import (
    SubmitClinicApplicationUseCase,
)
from app.modules.clinic_applications.application.use_cases.submit_lead import SubmitLeadUseCase
from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
    ClinicApplicationRepository,
)
from app.modules.identity.application.services.otp_service import OtpService
from app.modules.identity.presentation.deps import get_otp_service
from app.shared.notifications.deps import get_notifier
from app.shared.notifications.notifier import Notifier


def get_application_repository(
    session: AsyncSession = Depends(get_session),
) -> ClinicApplicationRepository:
    return ClinicApplicationRepository(session)


def get_submit_application_use_case(
    session: AsyncSession = Depends(get_session),
    applications: ClinicApplicationRepository = Depends(get_application_repository),
    otp: OtpService = Depends(get_otp_service),
) -> SubmitClinicApplicationUseCase:
    return SubmitClinicApplicationUseCase(session, applications, otp)


def get_team_notification_service(
    notifier: Notifier = Depends(get_notifier),
) -> TeamNotificationService:
    return TeamNotificationService(notifier)


def get_submit_lead_use_case(
    session: AsyncSession = Depends(get_session),
    applications: ClinicApplicationRepository = Depends(get_application_repository),
    notifier: TeamNotificationService = Depends(get_team_notification_service),
) -> SubmitLeadUseCase:
    return SubmitLeadUseCase(session, applications, notifier)


def get_list_applications_use_case(
    applications: ClinicApplicationRepository = Depends(get_application_repository),
) -> ListClinicApplicationsUseCase:
    return ListClinicApplicationsUseCase(applications)


def get_get_application_use_case(
    applications: ClinicApplicationRepository = Depends(get_application_repository),
) -> GetClinicApplicationUseCase:
    return GetClinicApplicationUseCase(applications)


def get_set_application_status_use_case(
    session: AsyncSession = Depends(get_session),
    applications: ClinicApplicationRepository = Depends(get_application_repository),
) -> SetApplicationStatusUseCase:
    return SetApplicationStatusUseCase(session, applications)
