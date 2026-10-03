"""Use case: перечитать состояние номера у Meta (имя, качество, доступ клиники).

Кнопка «Обновить» на экране подключения. Тот же запрос делает ежечасная
проверка, но клиника не должна ждать час, чтобы увидеть, что переподключение
помогло: успешный ответ снимает отметку «доступ не принимается».
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import NotFoundError, UpstreamError
from app.modules.whatsapp.application.services.connection_monitor import auth_rejected
from app.modules.whatsapp.application.services.credentials import (
    SELF_SERVICE,
    binding_token,
    clear_reconnect,
    flag_reconnect,
)
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import WhatsAppMode
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.whatsapp_cloud import MetaOnboardingClient, MetaOnboardingError


class RefreshConnectionUseCase:
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
        own = binding_token(binding)
        token = own or settings.wa_cloud_access_token
        if self._onboarding is None or not token:
            return binding  # спросить Meta нечем — показываем последнее известное
        now = datetime.now(UTC)
        try:
            info = await self._onboarding.phone_number(binding.phone_number_id, token)
        except MetaOnboardingError as exc:
            if own and binding.connected_via == SELF_SERVICE and auth_rejected(exc):
                flag_reconnect(binding, "token_invalid", now)
                await self._session.commit()
                return binding
            raise UpstreamError(
                "Meta не ответила — попробуйте обновить позже", code="meta_unavailable"
            ) from exc
        binding.verified_name = info.verified_name
        binding.quality_rating = info.quality_rating
        binding.status_checked_at = now
        if info.is_on_biz_app:
            binding.mode = WhatsAppMode.COEXISTENCE
        if binding.needs_reconnect_reason == "token_invalid":
            clear_reconnect(binding)
        await self._session.commit()
        return binding
