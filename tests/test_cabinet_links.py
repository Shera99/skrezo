"""Ссылки на кабинет: собираются от CABINET_URL, без настройки — не собираются."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.shared.cabinet_links import cabinet_host, cabinet_url, pay_url


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
