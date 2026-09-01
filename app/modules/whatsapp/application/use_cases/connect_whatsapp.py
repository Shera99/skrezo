"""Use-case: привязать номер WhatsApp к клинике (админка Skrezo)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import WhatsAppMode
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp


class ConnectWhatsAppUseCase:
    def __init__(self, session: AsyncSession, repository: WhatsAppRepository) -> None:
        self._session = session
        self._repository = repository

    async def execute(
        self,
        clinic_id: uuid.UUID,
        *,
        phone_number_id: str,
        display_phone: str,
        waba_id: str | None,
        mode: WhatsAppMode,
        active: bool,
    ) -> ClinicWhatsApp:
        if not await self._repository.clinic_exists(clinic_id):
            raise NotFoundError("Клиент не найден", code="client_not_found")
        taken = await self._repository.binding_by_phone_number_id(
            phone_number_id, include_inactive=True
        )
        if taken is not None and taken.clinic_id != clinic_id:
            # Номер Meta глобально один: два вебхука на один phone_number_id —
            # это перепутанные клиники, а не конфигурация.
            raise ConflictError(
                "Этот номер уже привязан к другой клинике", code="phone_number_taken"
            )
        binding = await self._repository.upsert_binding(
            clinic_id,
            phone_number_id=phone_number_id,
            display_phone=display_phone,
            waba_id=waba_id,
            mode=mode,
            active=active,
        )
        await self._session.commit()
        return binding
