"""Use-case: смена номера администратора клиники (Skrezo-админка, прямая установка).

Команда Skrezo доверенная, поэтому номер владельца меняется одним шагом без OTP:
проверяем существование клиента, дедуп номера по другим аккаунтам и применяем.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.clients.infrastructure.repositories.client_repository import ClientRepository
from app.shared.domain.value_objects import normalize_phone


class ChangeAdminPhoneUseCase:
    def __init__(self, session: AsyncSession, clients: ClientRepository) -> None:
        self._session = session
        self._clients = clients

    async def execute(self, clinic_id: uuid.UUID, new_phone_raw: str) -> str:
        phone = normalize_phone(new_phone_raw)
        owner = await self._clients.owner_account(clinic_id)
        if owner is None:
            raise NotFoundError("Клиент не найден", code="client_not_found")
        existing = await self._clients.account_by_phone(phone)
        if existing is not None and existing.id != owner.id:
            raise ConflictError("Этот номер уже используется", code="phone_already_used")
        await self._clients.update_account_phone(owner.id, phone)
        await self._session.commit()
        return phone
