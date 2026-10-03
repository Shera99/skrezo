"""Учётные данные номера клиники: свой бизнес-токен (Embedded Signup) или общий.

Номер, который клиника подключила сама, работает её токеном: наш общий токен
Skrezo к её WABA доступа не имеет. Номер, привязанный командой вручную, — общим
токеном платформы. Выбор делается здесь, в одном месте, а каждое место отправки
только просит «транспорт для этого номера».
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime

import structlog

from app.core.errors import AppError
from app.shared.crypto import SecretBoxError, secret_box
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.domain.value_objects import normalize_phone
from app.shared.whatsapp_cloud import WhatsAppCloudClient

logger = structlog.get_logger(__name__)

#: Пути подключения номера (``clinic_whatsapp.connected_via``).
MANUAL = "manual"
SELF_SERVICE = "embedded_signup"


def binding_token(binding: ClinicWhatsApp) -> str | None:
    """Расшифрованный бизнес-токен клиники; ``None`` — номер работает общим токеном."""
    if not binding.access_token_enc:
        return None
    try:
        return secret_box().decrypt(binding.access_token_enc)
    except SecretBoxError as exc:
        # Ключ сменили без перешифровки: номер клиники перестаёт отправлять
        # (общий токен к её WABA доступа не имеет) — в логе видно почему.
        logger.error(
            "wa_binding_token_unreadable", clinic_id=str(binding.clinic_id), error=str(exc)
        )
        return None


def client_for(client: WhatsAppCloudClient, binding: ClinicWhatsApp) -> WhatsAppCloudClient:
    """Транспорт от имени номера: токен клиники, если он есть, иначе общий.

    Без своего токена клиент возвращается как есть: подделкам транспорта в
    тестах не нужно уметь ``for_token``, пока у привязки нет токена.
    """
    token = binding_token(binding)
    return client if token is None else client.for_token(token)


def new_two_step_pin() -> str:
    """PIN двухэтапной проверки для регистрации нового номера в Cloud API."""
    return f"{secrets.randbelow(10**6):06d}"


def e164(display_phone: str) -> str:
    """Номер из формата Meta («+996 700 123-456») — в E.164, как ``wa_phone`` диалогов."""
    candidate = "+" + re.sub(r"\D", "", display_phone)
    try:
        return normalize_phone(candidate)
    except AppError:
        return candidate


def flag_reconnect(binding: ClinicWhatsApp, reason: str, now: datetime) -> bool:
    """Отметить «номер нужно переподключить»; ``False`` — отметка уже стояла."""
    if binding.needs_reconnect_at is not None:
        return False
    binding.needs_reconnect_at = now
    binding.needs_reconnect_reason = reason
    binding.reconnect_notified_at = None
    return True


def clear_reconnect(binding: ClinicWhatsApp) -> None:
    """Номер снова работает: снять отметку и разрешить будущие оповещения."""
    binding.needs_reconnect_at = None
    binding.needs_reconnect_reason = None
    binding.reconnect_notified_at = None
