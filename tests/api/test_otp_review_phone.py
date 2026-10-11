"""Тесты номера для проверки приложения в App Store / Google Play.

Ревьюер стора входит по OTP, но SIM-карты с номером у него нет, поэтому для
одного номера (``OTP_REVIEW_PHONE``) код фиксирован (``OTP_REVIEW_CODE``) и
никуда не отправляется.

Ключевые риски:

* фиксированный код не должен «протечь» на другие номера — у них код случайный
  и уходит по каналам доставки;
* полупустая настройка (номер без кода и наоборот) или код не той длины
  должны ронять старт, а не молча ничего не включать;
* код ревью живёт как обычный: верный — входит, неверный — нет.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.modules.identity.application.services.otp_delivery_service import OtpDeliveryService
from app.modules.identity.application.services.otp_service import OtpService
from app.modules.identity.domain.models import OtpPurpose
from tests.conftest import API, Resources

REVIEW_PHONE = "+996500400403"
REVIEW_CODE = "123456"


class _ChannelSpy:
    """Канал доставки, который только запоминает, кому что отправили."""

    name = "spy"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send(self, phone: str, code: str, purpose: OtpPurpose) -> None:
        self.sent.append((phone, code))


@pytest.fixture
def _review_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Включить режим ревью так, как его включит прод-``.env``."""
    monkeypatch.setattr(settings, "otp_review_phone", REVIEW_PHONE)
    monkeypatch.setattr(settings, "otp_review_code", REVIEW_CODE)


# ── Настройка ───────────────────────────────────────────────────────────────


def _settings(**values: Any) -> Settings:
    """Настройки только из аргументов — без локального ``.env``."""
    return Settings(_env_file=None, **values)


def test_review_phone_is_normalized_to_e164() -> None:
    """Номер можно вписать как угодно — сравнивается он в E.164."""
    configured = _settings(otp_review_phone="0500 400 403", otp_review_code=REVIEW_CODE)

    assert configured.otp_review_phone == REVIEW_PHONE


def test_review_mode_is_off_by_default() -> None:
    """Ничего не задано — режим выключен, старт не падает."""
    configured = _settings()

    assert configured.otp_review_phone == ""
    assert configured.otp_review_code == ""


@pytest.mark.parametrize(
    "values",
    [
        {"otp_review_phone": REVIEW_PHONE},  # номер без кода
        {"otp_review_code": REVIEW_CODE},  # код без номера
        {"otp_review_phone": REVIEW_PHONE, "otp_review_code": "12345"},  # не та длина
        {"otp_review_phone": REVIEW_PHONE, "otp_review_code": "12345a"},  # не цифры
        {"otp_review_phone": "12", "otp_review_code": REVIEW_CODE},  # не номер
    ],
)
def test_broken_review_settings_fail_at_startup(values: dict[str, str]) -> None:
    """Недоделанная настройка роняет старт, а не молча ничего не включает."""
    with pytest.raises(ValidationError):
        _settings(**values)


# ── Выпуск кода ─────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("_review_mode")
async def test_review_phone_gets_fixed_code_without_delivery(_resources: Resources) -> None:
    """Номер ревью получает известный код, и никуда его не отправляют."""
    spy = _ChannelSpy()
    otp = OtpService(_resources.fake, OtpDeliveryService([spy]))

    code = await otp.request_code(REVIEW_PHONE, OtpPurpose.LOGIN)

    assert code == REVIEW_CODE
    assert spy.sent == []
    assert await otp.verify_code(REVIEW_PHONE, REVIEW_CODE, OtpPurpose.LOGIN)


@pytest.mark.usefixtures("_review_mode")
async def test_other_phones_still_get_random_delivered_code(_resources: Resources) -> None:
    """Режим ревью касается одного номера: остальным код случайный и уходит каналом."""
    spy = _ChannelSpy()
    otp = OtpService(_resources.fake, OtpDeliveryService([spy]))

    code = await otp.request_code("+996700111222", OtpPurpose.LOGIN)

    assert spy.sent == [("+996700111222", code)]


# ── Вход через API ──────────────────────────────────────────────────────────


@pytest.mark.usefixtures("_review_mode")
async def test_reviewer_passes_otp_with_fixed_code(
    client: AsyncClient, _resources: Resources
) -> None:
    """Ревьюер вводит номер в любом виде и код 123456 — проверка кода проходит."""
    requested = await client.post(f"{API}/auth/otp/request", json={"phone": "0500400403"})
    assert requested.status_code == 200, requested.text

    wrong = await client.post(
        f"{API}/auth/otp/verify", json={"phone": REVIEW_PHONE, "code": "000000"}
    )
    assert wrong.status_code == 400, wrong.text

    verified = await client.post(
        f"{API}/auth/otp/verify", json={"phone": REVIEW_PHONE, "code": REVIEW_CODE}
    )
    assert verified.status_code == 200, verified.text
