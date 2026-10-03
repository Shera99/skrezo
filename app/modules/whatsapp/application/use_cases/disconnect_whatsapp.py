"""Use case: клиника отключает свой номер WhatsApp от Skrezo.

Наше приложение отписывается от вебхуков WABA клиники, токен и PIN стираются,
привязка выключается. Переписка, архив и контакты остаются: подключив номер
снова, клиника увидит всё как было. Приложение WhatsApp Business на телефоне
продолжает работать — отключение касается только Skrezo.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.modules.whatsapp.application.services.credentials import binding_token
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
    reset_self_service,
)
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.whatsapp_cloud import MetaOnboardingClient, MetaOnboardingError

logger = structlog.get_logger(__name__)


class DisconnectWhatsAppUseCase:
    def __init__(
        self,
        session: AsyncSession,
        repository: WhatsAppRepository,
        onboarding: MetaOnboardingClient | None,
    ) -> None:
        self._session = session
        self._repository = repository
        self._onboarding = onboarding

    async def execute(self, clinic_id: uuid.UUID) -> ClinicWhatsApp:
        binding = await self._repository.binding_by_clinic(clinic_id)
        if binding is None or not binding.active:
            raise NotFoundError("WhatsApp не подключён к клинике", code="whatsapp_not_connected")
        token = binding_token(binding)
        if token and binding.waba_id and self._onboarding is not None:
            try:
                await self._onboarding.unsubscribe_app(binding.waba_id, token)
            except MetaOnboardingError as exc:
                # Доступ мог быть уже отозван — у нас номер отключается всё равно.
                logger.warning("wa_unsubscribe_failed", clinic_id=str(clinic_id), error=str(exc))
        binding.active = False
        reset_self_service(binding)
        await self._session.commit()
        logger.info("wa_self_service_disconnected", clinic_id=str(clinic_id))
        return binding
