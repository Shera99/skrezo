"""Use-case: создание аккаунта клиента (клиника/соло) из админ-панели.

Тонкая обёртка над провижинингом (`clinics.CreateClinicUseCase`): добавляет
дружественный 409 с именем владельца при занятом номере (спека §1.3/1.7).
"""

from __future__ import annotations

from app.core.errors import ConflictError
from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository
from app.modules.clinics.application.services.provisioning_service import (
    ProvisionCommand,
    ProvisionResult,
)
from app.modules.clinics.application.use_cases.create_clinic import CreateClinicUseCase
from app.shared.domain.value_objects import normalize_phone


class CreateAccountUseCase:
    def __init__(self, create_clinic: CreateClinicUseCase, clients: ClientRepository) -> None:
        self._create_clinic = create_clinic
        self._clients = clients

    async def execute(self, command: ProvisionCommand) -> ProvisionResult:
        phone = normalize_phone(command.admin_phone)
        owner_name = await self._clients.owner_name_by_phone(phone)
        if owner_name is not None:
            raise ConflictError(
                f"Номер уже используется (владелец: {owner_name})", code="phone_already_used"
            )
        return await self._create_clinic.execute(command)
