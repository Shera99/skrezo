"""Use-case: синхронизировать шаблоны клиники из кабинета Meta."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, ValidationAppError
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.models.whatsapp_template import WhatsAppTemplate
from app.shared.whatsapp_cloud import WhatsAppCloudClient


class SyncTemplatesUseCase:
    def __init__(
        self,
        session: AsyncSession,
        repository: WhatsAppRepository,
        client: WhatsAppCloudClient,
    ) -> None:
        self._session = session
        self._repository = repository
        self._client = client

    async def execute(self, clinic_id: uuid.UUID) -> list[WhatsAppTemplate]:
        """Спросить Meta и привести локальный каталог к её ответу.

        ``None`` от клиента (нет кредов либо Graph API не ответил) каталог не
        трогает: стирать шаблоны из-за недоступности API нельзя — клиника
        осталась бы без единственного способа писать в закрытое окно.
        """
        binding = await self._repository.binding_by_clinic(clinic_id)
        if binding is None or not binding.active:
            raise ConflictError(
                "WhatsApp не подключён к клинике", code="whatsapp_not_connected"
            )
        if not binding.waba_id:
            raise ValidationAppError(
                "У привязки не указан WABA — без него шаблоны спросить не у кого",
                code="waba_id_missing",
            )
        fetched = await self._client.fetch_templates(binding.waba_id)
        if fetched is None:
            raise AppError(
                "Синхронизация шаблонов недоступна: Graph API не ответил "
                "(или канал работает без кредов Meta)",
                code="wa_templates_unavailable",
                status_code=502,
            )
        await self._repository.replace_templates(clinic_id, fetched)
        await self._session.commit()
        return await self._repository.list_templates(clinic_id)
