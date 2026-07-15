"""Зависимости раздела «Клиенты»."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.modules.clients.application.use_cases.archive_client import ArchiveClientUseCase
from app.modules.clients.application.use_cases.change_admin_phone import ChangeAdminPhoneUseCase
from app.modules.clients.application.use_cases.create_account import CreateAccountUseCase
from app.modules.clients.application.use_cases.get_client import GetClientUseCase
from app.modules.clients.application.use_cases.list_clients import ListClientsUseCase
from app.modules.clients.application.use_cases.phone_check import PhoneCheckUseCase
from app.modules.clients.application.use_cases.reset_admin_password import (
    ResetAdminPasswordUseCase,
)
from app.modules.clients.application.use_cases.status_counts import StatusCountsUseCase
from app.modules.clients.application.use_cases.update_client import UpdateClientUseCase
from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository
from app.modules.clinics.application.use_cases.create_clinic import CreateClinicUseCase
from app.modules.clinics.presentation.deps import get_create_clinic_use_case


def get_client_repository(session: AsyncSession = Depends(get_session)) -> ClientRepository:
    return ClientRepository(session)


def get_create_account_use_case(
    create_clinic: CreateClinicUseCase = Depends(get_create_clinic_use_case),
    clients: ClientRepository = Depends(get_client_repository),
) -> CreateAccountUseCase:
    return CreateAccountUseCase(create_clinic, clients)


def get_phone_check_use_case(
    clients: ClientRepository = Depends(get_client_repository),
) -> PhoneCheckUseCase:
    return PhoneCheckUseCase(clients)


def get_status_counts_use_case(
    clients: ClientRepository = Depends(get_client_repository),
) -> StatusCountsUseCase:
    return StatusCountsUseCase(clients)


def get_list_clients_use_case(
    clients: ClientRepository = Depends(get_client_repository),
) -> ListClientsUseCase:
    return ListClientsUseCase(clients)


def get_get_client_use_case(
    clients: ClientRepository = Depends(get_client_repository),
) -> GetClientUseCase:
    return GetClientUseCase(clients)


def get_update_client_use_case(
    session: AsyncSession = Depends(get_session),
    clients: ClientRepository = Depends(get_client_repository),
) -> UpdateClientUseCase:
    return UpdateClientUseCase(session, clients)


def get_change_admin_phone_use_case(
    session: AsyncSession = Depends(get_session),
    clients: ClientRepository = Depends(get_client_repository),
) -> ChangeAdminPhoneUseCase:
    return ChangeAdminPhoneUseCase(session, clients)


def get_reset_admin_password_use_case(
    session: AsyncSession = Depends(get_session),
    clients: ClientRepository = Depends(get_client_repository),
) -> ResetAdminPasswordUseCase:
    return ResetAdminPasswordUseCase(session, clients)


def get_archive_client_use_case(
    session: AsyncSession = Depends(get_session),
    clients: ClientRepository = Depends(get_client_repository),
) -> ArchiveClientUseCase:
    return ArchiveClientUseCase(session, clients)
