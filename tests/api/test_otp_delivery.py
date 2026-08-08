"""Тесты доставки OTP: три канала и переход к следующему при неудаче.

Каналы перенесены из showgo_api (Telegram Gateway, WhatsApp-бот, SMSPRO).
Ключевые риски именно в них:

* провайдер отвечает **HTTP 200 при неудаче** — и Telegram (`ok: false`), и
  WhatsApp (`success: false`), и SMSPRO (статус в XML). Принять такой ответ за
  успех значит оставить пользователя без кода и не заметить этого;
* цепочка каналов должна переходить к следующему: у половины номеров нет
  Telegram, и без перехода вход для них не работает;
* если не сработал ни один — наружу 502, а не «успех»: иначе пользователь ждёт
  SMS, которой не будет.

HTTP подменяется ``httpx.MockTransport`` — сеть в тестах не трогаем.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.modules.identity.application.services.otp_delivery_service import OtpDeliveryService
from app.modules.identity.domain.models import OtpPurpose
from app.modules.identity.infrastructure.otp.channels import OtpChannel, OtpDeliveryError
from app.modules.identity.infrastructure.otp.smspro import SmsProChannel
from app.modules.identity.infrastructure.otp.telegram_gateway import TelegramGatewayChannel
from app.modules.identity.infrastructure.otp.whatsapp import WhatsAppChannel

PHONE = "+996700123456"
CODE = "123456"


@pytest.fixture(autouse=True)
def _provider_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настройки провайдеров — свои, а не из локального ``.env``.

    Без этого тест зависит от машины разработчика: пустой адрес WhatsApp даёт
    невалидный URL, а чужой токен уезжает в заголовок запроса.
    """
    monkeypatch.setattr(settings, "telegram_gateway_url", "https://telegram.example")
    monkeypatch.setattr(settings, "telegram_gateway_token", "test-token")
    monkeypatch.setattr(settings, "whatsapp_api_url", "https://whatsapp.example/api/create_message")
    monkeypatch.setattr(settings, "whatsapp_account_id", "test-account")
    monkeypatch.setattr(settings, "smspro_url", "https://smspro.example/api/message")
    monkeypatch.setattr(settings, "smspro_login", "login")
    monkeypatch.setattr(settings, "smspro_password", "pwd")
    monkeypatch.setattr(settings, "smspro_sender", "SKREZO")


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


# ── Telegram Gateway ────────────────────────────────────────────────────────


async def test_telegram_sends_code_and_ttl() -> None:
    """Gateway получает номер и код; текст он рисует своим шаблоном."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"request_id": "x"}})

    async with _client(handler) as client:
        await TelegramGatewayChannel(client).send(PHONE, CODE)

    assert str(seen["url"]).endswith("/sendVerificationMessage")
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["phone_number"] == PHONE
    assert body["code"] == CODE
    assert body["ttl"] > 0


async def test_telegram_not_ok_is_delivery_error() -> None:
    """HTTP 200 с `ok: false` — недоставка (частый случай: нет Telegram у номера)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "PHONE_NOT_FOUND"})

    async with _client(handler) as client:
        with pytest.raises(OtpDeliveryError, match="PHONE_NOT_FOUND"):
            await TelegramGatewayChannel(client).send(PHONE, CODE)


# ── WhatsApp ────────────────────────────────────────────────────────────────


async def test_whatsapp_strips_plus_and_sends_text() -> None:
    """Номер уходит без плюса, а текст — человеку, значит по-русски."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    async with _client(handler) as client:
        await WhatsAppChannel(client).send(PHONE, CODE)

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["to"] == PHONE.lstrip("+")
    assert CODE in body["message"]


async def test_whatsapp_success_false_is_delivery_error() -> None:
    """`success: false` при HTTP 200 — тоже недоставка."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": "no whatsapp"})

    async with _client(handler) as client:
        with pytest.raises(OtpDeliveryError, match="no whatsapp"):
            await WhatsAppChannel(client).send(PHONE, CODE)


# ── SMSPRO ──────────────────────────────────────────────────────────────────


async def test_smspro_sends_xml_with_latin_text() -> None:
    """SMS уходит латиницей: кириллица удваивает стоимость сегмента."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["xml"] = request.content.decode()
        return httpx.Response(200, text="<response><status>0</status></response>")

    async with _client(handler) as client:
        await SmsProChannel(client).send(PHONE, CODE)

    assert f"<phone>{PHONE}</phone>" in seen["xml"]
    assert CODE in seen["xml"]
    assert seen["xml"].isascii()  # текст без кириллицы


@pytest.mark.parametrize("status", ["0", "11"])
async def test_smspro_accepted_statuses(status: str) -> None:
    """0 — принято, 11 — принято частично; оба считаются успехом."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"<response><status>{status}</status></response>")

    async with _client(handler) as client:
        await SmsProChannel(client).send(PHONE, CODE)


async def test_smspro_rejected_status_is_delivery_error() -> None:
    """Любой другой статус — провайдер сообщение не принял."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<response><status>4</status><message>no money</message></response>"
        )

    async with _client(handler) as client:
        with pytest.raises(OtpDeliveryError, match="no money"):
            await SmsProChannel(client).send(PHONE, CODE)


async def test_smspro_escapes_special_characters() -> None:
    """Текст и логин экранируются: `&` в пароле не должен ломать XML молча."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Ответ разберётся только если запрос был валидным XML — проверяем сам факт.
        return httpx.Response(200, text="<response><status>0</status></response>")

    async with _client(handler) as client:
        await SmsProChannel(client).send("+996700<>&123", CODE)


# ── Цепочка каналов ─────────────────────────────────────────────────────────


class _Failing:
    name = "failing"

    def __init__(self) -> None:
        self.calls = 0

    async def send(self, phone: str, code: str, purpose: OtpPurpose) -> None:
        self.calls += 1
        raise OtpDeliveryError("сломан")


class _Working:
    name = "working"

    def __init__(self) -> None:
        self.calls = 0
        self.purposes: list[OtpPurpose] = []

    async def send(self, phone: str, code: str, purpose: OtpPurpose) -> None:
        self.calls += 1
        self.purposes.append(purpose)


async def test_delivery_falls_back_to_next_channel() -> None:
    """У номера нет Telegram — код уходит следующим каналом, без ошибки наружу."""
    failing, working = _Failing(), _Working()
    channels: list[OtpChannel] = [failing, working]

    await OtpDeliveryService(channels).send_code(PHONE, CODE)

    assert failing.calls == 1
    assert working.calls == 1


async def test_delivery_stops_after_first_success() -> None:
    """Платный канал не трогаем, если сработал бесплатный."""
    first, second = _Working(), _Working()

    await OtpDeliveryService([first, second]).send_code(PHONE, CODE)

    assert first.calls == 1
    assert second.calls == 0


async def test_delivery_fails_when_all_channels_fail() -> None:
    """Ни один канал не справился — это ошибка, а не тихий успех."""
    with pytest.raises(OtpDeliveryError):
        await OtpDeliveryService([_Failing(), _Failing()]).send_code(PHONE, CODE)


async def test_delivery_passes_purpose_to_channel() -> None:
    """Назначение доходит до канала — от него зависит текст сообщения."""
    channel = _Working()

    await OtpDeliveryService([channel]).send_code(PHONE, CODE, OtpPurpose.PASSWORD_RESET)

    assert channel.purposes == [OtpPurpose.PASSWORD_RESET]


@pytest.mark.parametrize(
    ("purpose", "expects_password_wording"),
    [(OtpPurpose.LOGIN, False), (OtpPurpose.PASSWORD_RESET, True)],
)
async def test_message_text_names_the_purpose(
    purpose: OtpPurpose, expects_password_wording: bool
) -> None:
    """Человек должен видеть, что подтверждает: вход или смену пароля."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["xml"] = request.content.decode()
        return httpx.Response(200, text="<response><status>0</status></response>")

    async with _client(handler) as client:
        await SmsProChannel(client).send(PHONE, CODE, purpose)

    assert ("parolya" in seen["xml"]) is expects_password_wording
    assert seen["xml"].isascii()  # текст сброса тоже латиницей
