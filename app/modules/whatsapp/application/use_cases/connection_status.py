"""Use case: состояние номера WhatsApp клиники и параметры окна подключения Meta."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.config import settings
from app.modules.whatsapp.domain.connection import (
    SIGNUP_FEATURE_TYPE,
    SIGNUP_SESSION_INFO_VERSION,
)
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.crypto import SecretBoxError, secret_box
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp


@dataclass(frozen=True, slots=True)
class SignupConfig:
    """Что нужно кабинету, чтобы открыть окно Meta (значения не секретные)."""

    available: bool
    app_id: str | None
    config_id: str | None
    graph_api_version: str | None
    feature_type: str = SIGNUP_FEATURE_TYPE
    session_info_version: str = SIGNUP_SESSION_INFO_VERSION


def signup_config() -> SignupConfig:
    """Окно доступно, когда настроены приложение Meta, конфигурация и ключ шифрования."""
    try:
        encryption = secret_box().configured
    except SecretBoxError:
        encryption = False
    available = bool(
        settings.wa_cloud_app_id
        and settings.wa_cloud_app_secret
        and settings.wa_embedded_signup_config_id
        and encryption
    )
    if not available:
        return SignupConfig(available=False, app_id=None, config_id=None, graph_api_version=None)
    return SignupConfig(
        available=True,
        app_id=settings.wa_cloud_app_id,
        config_id=settings.wa_embedded_signup_config_id,
        # Версия Graph API — последний сегмент WA_CLOUD_API_URL (…/v23.0).
        graph_api_version=settings.wa_cloud_api_url.rstrip("/").rsplit("/", 1)[-1],
    )


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    binding: ClinicWhatsApp | None
    signup: SignupConfig


class GetConnectionStatusUseCase:
    def __init__(self, repository: WhatsAppRepository) -> None:
        self._repository = repository

    async def execute(self, clinic_id: uuid.UUID) -> ConnectionStatus:
        return ConnectionStatus(
            binding=await self._repository.binding_by_clinic(clinic_id),
            signup=signup_config(),
        )
