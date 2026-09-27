"""Ссылки на кабинет: собираются от CABINET_URL, без настройки — не собираются."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.shared.cabinet_links import (
    assistant_learn_url,
    cabinet_host,
    cabinet_url,
    chat_url,
    event_url,
    patient_url,
    pay_url,
    schedule_url,
)


def test_links_are_built_from_cabinet_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cabinet_url", "https://office.skrezo.com")
    payment_id = uuid.UUID("7b0f6a3e-1d2c-4a5b-9c8d-0e1f2a3b4c5d")
    assert pay_url() == "https://office.skrezo.com/pay"
    assert pay_url(payment_id) == f"https://office.skrezo.com/pay?payment={payment_id}"
    assert cabinet_url("/schedule") == "https://office.skrezo.com/schedule"
    assert cabinet_host() == "office.skrezo.com"


def test_no_cabinet_url_means_no_links(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cabinet_url", "")
    assert pay_url() is None and pay_url(uuid.uuid4()) is None
    assert cabinet_url("pay") is None and cabinet_host() is None


def test_section_links_are_plain_cabinet_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Разделы кабинета — прямые адреса на CABINET_URL без редиректов (universal links)."""
    monkeypatch.setattr(settings, "cabinet_url", "https://office.skrezo.com")
    some_id = uuid.UUID("7b0f6a3e-1d2c-4a5b-9c8d-0e1f2a3b4c5d")
    assert patient_url(some_id) == f"https://office.skrezo.com/patients/{some_id}"
    assert chat_url(some_id) == f"https://office.skrezo.com/chat/{some_id}"
    assert assistant_learn_url(some_id) == f"https://office.skrezo.com/assistant/learn/{some_id}"
    assert schedule_url() == "https://office.skrezo.com/schedule"


def test_event_url_maps_notifications_to_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Одно правило для инбокса: диалог → чат, приём и сводка → расписание, платёж → пациент."""
    monkeypatch.setattr(settings, "cabinet_url", "https://office.skrezo.com")
    conversation_id, patient_id = uuid.uuid4(), uuid.uuid4()
    assert event_url("conversation.inbound", "conversation", conversation_id) == (
        f"https://office.skrezo.com/chat/{conversation_id}"
    )
    assert event_url("ai.escalated", "conversation", conversation_id) == (
        f"https://office.skrezo.com/chat/{conversation_id}"
    )
    assert event_url("appointment.created", "appointment", uuid.uuid4()) == (
        "https://office.skrezo.com/schedule"
    )
    assert event_url("digest.morning", None, None) == "https://office.skrezo.com/schedule"
    assert event_url("payment.created", "payment", uuid.uuid4(), patient_id=patient_id) == (
        f"https://office.skrezo.com/patients/{patient_id}"
    )
    assert event_url("payment.created", "payment", uuid.uuid4()) is None
    assert event_url("subscription.expiring", None, None) == "https://office.skrezo.com/pay"
    assert event_url("staff.joined", "membership", uuid.uuid4()) is None

    monkeypatch.setattr(settings, "cabinet_url", "")
    assert event_url("conversation.inbound", "conversation", conversation_id) is None


def test_cabinet_url_setting_drops_trailing_slash() -> None:
    """Слеш в конце из .env не даёт двойного «//pay» в ссылке."""
    assert Settings(cabinet_url=" https://office.skrezo.com/ ").cabinet_url == (
        "https://office.skrezo.com"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "office.skrezo.com",  # без схемы: возврат с оплаты стал бы «office.skrezo.com/pay»
        "https://office.skrezo.com/app",
        "https://office.skrezo.com?utm=1",
        "ftp://office.skrezo.com",
    ],
)
def test_cabinet_url_must_be_scheme_and_host(raw: str) -> None:
    with pytest.raises(ValidationError, match="CABINET_URL"):
        Settings(cabinet_url=raw)


@pytest.mark.parametrize("raw", ["http://office.skrezo.com", "https://www.office.skrezo.com"])
def test_production_cabinet_url_is_https_without_www(raw: str) -> None:
    """iOS открывает приложение только по прямой https-ссылке на привязанный домен."""
    with pytest.raises(ValidationError, match="https и без www"):
        Settings(app_env="production", cabinet_url=raw)
    assert Settings(app_env="local", cabinet_url=raw).cabinet_url == raw  # разработка
