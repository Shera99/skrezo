"""Тексты оплаты: ссылка на /pay в уведомлениях о подписке и домен в SMS с кодом."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.modules.identity.domain.models import OtpPurpose
from app.modules.identity.domain.otp_message import sms_text
from app.modules.identity.infrastructure.otp.smspro import SmsProChannel
from app.modules.subscriptions.domain.messages import (
    balance_low_text,
    paid_period_expired_text,
    period_ending_text,
    trial_ending_text,
    trial_expired_text,
)
from app.shared.notifications.smspro import SmsProTextChannel

PAY = "https://office.skrezo.com/pay"

#: Базовый алфавит GSM-7 (без таблицы расширения): только эти символы
#: оставляют SMS в сегменте на 160 символов.
_GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)


def test_owner_texts_link_to_pay_page() -> None:
    texts = [
        trial_ending_text("Дента", 3, pay_url=PAY),
        trial_expired_text("Дента", pay_url=PAY),
        period_ending_text("Дента", 3, pay_url=PAY),
        paid_period_expired_text("Дента", pay_url=PAY),
        balance_low_text("Дента", 150000, 2, pay_url=PAY),
    ]
    for text in texts:
        assert text.endswith(PAY), text
        assert "Свяжитесь с нами" not in text
    # Решение 26.09: оплата на триале не сжигает оставшиеся дни — текст это обещает.
    assert "оставшиеся дни пробного периода сохранятся" in texts[0]


def test_owner_texts_without_cabinet_keep_contact_us() -> None:
    assert trial_ending_text("Дента", 3).endswith("Свяжитесь с нами, чтобы продолжить.")
    assert "http" not in period_ending_text("Дента", 3)
    assert balance_low_text("Дента", 150000, 0).endswith("и доступ продлится сам.")


@pytest.mark.parametrize("purpose", [OtpPurpose.LOGIN, OtpPurpose.PASSWORD_RESET])
def test_sms_ends_with_domain_bound_code_and_stays_one_segment(purpose: OtpPurpose) -> None:
    text = sms_text("123456", purpose, bound_domain="office.skrezo.com")
    assert text.splitlines()[-1] == "@office.skrezo.com #123456"
    assert len(text) <= 160 and set(text) <= _GSM7


def test_sms_without_domain_has_no_binding_line() -> None:
    assert "\n" not in sms_text("123456") and "@" not in sms_text("123456")


@pytest.mark.asyncio
async def test_sms_channel_binds_only_login_code_to_cabinet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Домен — только у кода входа: сброс пароля делают админы на другом домене."""
    monkeypatch.setattr(settings, "cabinet_url", "https://office.skrezo.com")
    sent: list[str] = []

    async def capture(self: SmsProTextChannel, phone: str, text: str) -> None:
        sent.append(text)

    monkeypatch.setattr(SmsProTextChannel, "send_text", capture)
    channel = SmsProChannel()
    await channel.send("+996700000000", "123456", OtpPurpose.LOGIN)
    await channel.send("+996700000000", "654321", OtpPurpose.PASSWORD_RESET)
    assert sent[0].splitlines()[-1] == "@office.skrezo.com #123456"
    assert "@" not in sent[1] and "\n" not in sent[1]
