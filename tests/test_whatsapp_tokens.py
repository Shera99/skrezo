"""Токены номеров: шифрование в базе и выбор токена транспорта (Embedded Signup)."""

from __future__ import annotations

import logging

import httpx
import pytest
from cryptography.fernet import Fernet

from app.core.logging import configure_logging
from app.modules.whatsapp.application.services.credentials import e164
from app.shared.crypto import SecretBox, SecretBoxError
from app.shared.whatsapp_cloud.graph_client import GraphWhatsAppClient
from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient


def test_secret_box_round_trip_and_rotation() -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    sealed = SecretBox(old).encrypt("EAAG-token")
    assert sealed != "EAAG-token" and SecretBox(old).decrypt(sealed) == "EAAG-token"
    # Ротация: новый ключ первым — старые шифротексты читаются, новые пишутся им.
    rotated = SecretBox(f"{new},{old}")
    assert rotated.decrypt(sealed) == "EAAG-token"
    assert SecretBox(new).decrypt(rotated.encrypt("x")) == "x"


def test_secret_box_refuses_without_or_with_wrong_key() -> None:
    sealed = SecretBox(Fernet.generate_key().decode()).encrypt("secret")
    with pytest.raises(SecretBoxError):
        SecretBox(Fernet.generate_key().decode()).decrypt(sealed)
    with pytest.raises(SecretBoxError):
        SecretBox("").encrypt("secret")
    with pytest.raises(SecretBoxError):
        SecretBox("not-a-fernet-key")
    assert SecretBox("").configured is False


async def test_graph_client_for_token_shares_pool_and_caches() -> None:
    async with httpx.AsyncClient() as pool:
        shared = GraphWhatsAppClient("https://graph.example/v23.0", "skrezo-token", client=pool)
        assert shared.for_token(None) is shared and shared.for_token("skrezo-token") is shared
        clinic = shared.for_token("clinic-token")
        assert clinic is not shared and clinic is shared.for_token("clinic-token")
        assert clinic._client is pool and clinic._headers() == {
            "Authorization": "Bearer clinic-token"
        }


def test_logging_client_stays_logging_with_clinic_token() -> None:
    fallback = LoggingWhatsAppClient()
    assert fallback.for_token("clinic-token") is fallback


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("+996 700 123-456", "+996700123456"), ("996555400400", "+996555400400")],
)
def test_meta_display_phone_to_e164(raw: str, expected: str) -> None:
    assert e164(raw) == expected


def test_http_client_does_not_log_request_urls() -> None:
    """URL обмена кода несёт секрет приложения Meta — httpx не должен его логировать."""
    configure_logging()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
