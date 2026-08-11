"""Тесты уведомлений: транспорты, доставка и три её потребителя.

Ключевые риски:

* провайдер отвечает **HTTP 200 при неудаче** (``success: false`` у WhatsApp,
  статус в XML у SMSPRO, ``ok: false`` у Bot API) — принять это за успех значит
  молча не доставить сообщение;
* недоставка не должна отменять сделанное: сотрудник добавлен, клиника создана,
  лид записан — уведомление про это вторично.

Сеть не трогаем: HTTP подменяется ``httpx.MockTransport``, а в API-тестах
подменяется сам :func:`get_notifier`. Постановка в очередь и работа воркера
проверяются в ``test_jobs_api``.
"""

from __future__ import annotations

import itertools
import json

import httpx
import pytest
from httpx import AsyncClient

from app.core.jobs import JobQueue
from app.main import app
from app.modules.leads.domain.messages import new_lead_text
from app.shared.db.models.lead import Lead
from app.shared.notifications.channels import MessageDeliveryError, TextChannel
from app.shared.notifications.delivery import TextDeliveryService
from app.shared.notifications.deps import get_notifier
from app.shared.notifications.notifier import Notifier
from app.shared.notifications.smspro import SmsProTextChannel
from app.shared.notifications.telegram_chat import TelegramChatChannel
from app.shared.notifications.whatsapp import WhatsAppTextChannel
from tests.conftest import API, Resources, otp_login

PHONE = "+996700123456"
TEXT = "Skrezo: тестовое сообщение"

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070094{next(_phone_seq):04d}"


def _client(handler: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


class _Recorder:
    """Канал-«записная книжка»: помнит отправленное, при желании — падает."""

    name = "recorder"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self._fail = fail

    async def send_text(self, phone: str, text: str) -> None:
        self.sent.append((phone, text))
        if self._fail:
            raise MessageDeliveryError("recorder: сломан")


class _ChatRecorder:
    name = "chat-recorder"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self._fail = fail

    async def send(self, text: str) -> None:
        self.sent.append(text)
        if self._fail:
            raise MessageDeliveryError("chat: сломан")


# ── Транспорты ──────────────────────────────────────────────────────────────


async def test_whatsapp_sends_text_without_plus() -> None:
    """Номер уходит без плюса, текст — как есть."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    async with _client(handler) as client:
        await WhatsAppTextChannel(
            api_url="https://whatsapp.example/send",
            account_id="acc",
            timeout=1.0,
            client=client,
        ).send_text(PHONE, TEXT)

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["to"] == PHONE.lstrip("+")
    assert body["message"] == TEXT


async def test_whatsapp_success_false_is_delivery_error() -> None:
    """HTTP 200 с `success: false` — недоставка, а не успех."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "message": "no whatsapp"})

    async with _client(handler) as client:
        with pytest.raises(MessageDeliveryError, match="no whatsapp"):
            await WhatsAppTextChannel(
                api_url="https://whatsapp.example/send",
                account_id="acc",
                timeout=1.0,
                client=client,
            ).send_text(PHONE, TEXT)


def _sms(client: httpx.AsyncClient) -> SmsProTextChannel:
    return SmsProTextChannel(
        url="https://smspro.example/api/message",
        login="login",
        password="p&ssword",  # амперсанд: XML не должен ломаться
        sender="SKREZO",
        timeout=1.0,
        client=client,
    )


async def test_smspro_sends_xml_with_text() -> None:
    """Текст и номер уходят внутри XML; спецсимволы экранируются."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["xml"] = request.content.decode()
        return httpx.Response(200, text="<response><status>0</status></response>")

    async with _client(handler) as client:
        await _sms(client).send_text(PHONE, "Клиника «А&Б»")

    assert f"<phone>{PHONE}</phone>" in seen["xml"]
    assert "&amp;" in seen["xml"]  # и в пароле, и в тексте


async def test_smspro_rejected_status_is_delivery_error() -> None:
    """Статус не из принятых — провайдер сообщение не взял."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<response><status>4</status><message>no money</message></response>"
        )

    async with _client(handler) as client:
        with pytest.raises(MessageDeliveryError, match="no money"):
            await _sms(client).send_text(PHONE, TEXT)


async def test_telegram_chat_posts_to_bot_api() -> None:
    """Сообщение уходит в ``sendMessage`` бота с нужным chat_id."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as client:
        await TelegramChatChannel(
            bot_token="123:abc",
            chat_id="-100500",
            timeout=1.0,
            api_url="https://telegram.example",
            client=client,
        ).send(TEXT)

    assert str(seen["url"]).endswith("/bot123:abc/sendMessage")
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["chat_id"] == "-100500"
    assert body["text"] == TEXT


async def test_telegram_chat_not_ok_is_delivery_error() -> None:
    """`ok: false` при HTTP 200 — тоже недоставка."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "chat not found"})

    async with _client(handler) as client:
        with pytest.raises(MessageDeliveryError, match="chat not found"):
            await TelegramChatChannel(
                bot_token="123:abc",
                chat_id="-1",
                timeout=1.0,
                api_url="https://telegram.example",
                client=client,
            ).send(TEXT)


# ── Цепочка каналов ─────────────────────────────────────────────────────────


async def test_delivery_falls_back_to_next_channel() -> None:
    """Нет WhatsApp — сообщение уходит SMS, без ошибки наружу."""
    failing, working = _Recorder(fail=True), _Recorder()
    channels: list[TextChannel] = [failing, working]

    await TextDeliveryService(channels).send(PHONE, TEXT)

    assert working.sent == [(PHONE, TEXT)]


async def test_delivery_stops_after_first_success() -> None:
    """Платный канал не трогаем, если сработал бесплатный."""
    first, second = _Recorder(), _Recorder()

    await TextDeliveryService([first, second]).send(PHONE, TEXT)

    assert first.sent and not second.sent


async def test_delivery_fails_when_all_channels_fail() -> None:
    """Ни один канал не справился — это ошибка, а не тихий успех."""
    with pytest.raises(MessageDeliveryError):
        await TextDeliveryService([_Recorder(fail=True), _Recorder(fail=True)]).send(PHONE, TEXT)


# ── Notifier: проглатывание ошибок ──────────────────────────────────────────
#
# Постановка в очередь и работа воркера — в ``test_jobs_api``; здесь только то,
# что видно вызывающей операции.


async def test_notifier_swallows_delivery_error() -> None:
    """Недоставка не всплывает наружу: операция уже совершена."""
    await Notifier(JobQueue(None), TextDeliveryService([_Recorder(fail=True)])).notify_phone(
        PHONE, TEXT, event="test"
    )  # не должно бросить


async def test_notifier_without_team_chat_says_nothing() -> None:
    """Чат команды не настроен — событие в лог, без попытки отправки (dev-стенд)."""
    chat = _ChatRecorder()

    await Notifier(JobQueue(None), TextDeliveryService([_Recorder()])).notify_team(
        "текст", event="test"
    )

    assert chat.sent == []


# ── Три потребителя: сообщение уходит по факту операции ─────────────────────


def _override_notifier(phones: _Recorder, chat: _ChatRecorder) -> None:
    """Подменить отправителя на записные книжки (сбрасывает ``_resources``).

    Очередь заведомо недоступна (``JobQueue(None)``) — значит доставка идёт на
    месте и к моменту ответа уже случилась, а проверка не зависит от воркера.
    """
    app.dependency_overrides[get_notifier] = lambda: Notifier(
        JobQueue(None), TextDeliveryService([phones]), chat
    )


async def test_new_clinic_owner_gets_login_message(
    admin_client: AsyncClient, _resources: Resources
) -> None:
    """Владельцу созданной клиники уходит номер и способ входа (без секрета)."""
    phones, chat = _Recorder(), _ChatRecorder()
    _override_notifier(phones, chat)

    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": phone,
            "name": "Клиника Уведомлений",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text

    assert len(phones.sent) == 1
    sent_phone, text = phones.sent[0]
    assert sent_phone.endswith(phone[1:])
    assert "Клиника Уведомлений" in text
    assert "код" in text  # вход по коду, пароля в сообщении нет
    assert "пароль" not in text.lower()


async def test_added_staff_gets_login_message(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Добавленному сотруднику уходит сообщение о доступе (пароля в нём нет)."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]
    owner_phone = _phone()
    clinic = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": owner_phone,
            "name": "Клиника Сотрудников",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert clinic.status_code == 201, clinic.text
    owner_headers = await otp_login(client, owner_phone)

    # Подменяем отправителя только теперь: уведомление о создании клиники уже позади.
    phones, chat = _Recorder(), _ChatRecorder()
    _override_notifier(phones, chat)

    staff_phone = _phone()
    added = await client.post(
        f"{API}/clinic/staff",
        headers=owner_headers,
        json={"fullName": "Врач", "phone": staff_phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text

    assert len(phones.sent) == 1
    sent_phone, text = phones.sent[0]
    assert sent_phone.endswith(staff_phone[1:])
    assert "Клиника Сотрудников" in text
    assert "код" in text  # вход по коду, пароля в сообщении нет
    assert "пароль" not in text.lower()


async def test_new_lead_goes_to_team_chat(client: AsyncClient, _resources: Resources) -> None:
    """Лид попадает в чат команды, а не на телефон."""
    phones, chat = _Recorder(), _ChatRecorder()
    _override_notifier(phones, chat)

    created = await client.post(
        f"{API}/leads",
        json={
            "fullName": "Заявитель",
            "phone": _phone(),
            "clinicName": "Клиника Лидов",
            "doctorsCountRange": "2-5",
        },
    )
    assert created.status_code == 201, created.text

    assert phones.sent == []
    assert len(chat.sent) == 1
    assert "Клиника Лидов" in chat.sent[0]
    assert "2-5" in chat.sent[0]


async def test_delivery_failure_does_not_break_the_operation(
    admin_client: AsyncClient, _resources: Resources
) -> None:
    """Провайдер лежит — клиника всё равно создана, ответ 201."""
    phones, chat = _Recorder(fail=True), _ChatRecorder(fail=True)
    _override_notifier(phones, chat)

    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": _phone(),
            "name": "Клиника Без Связи",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    assert phones.sent  # попытка была
    assert created.json()["adminPhone"]


# ── Текст сообщения ─────────────────────────────────────────────────────────


def test_lead_message_lists_optional_fields_only_when_filled() -> None:
    """Пустые поля лида в карточку не лезут — иначе в чате пустые строки."""
    lead = Lead(
        full_name="Заявитель",
        phone="+996700123456",
        clinic_name="Клиника",
        doctors_count_range=None,
        comment=None,
    )
    text = new_lead_text(lead)

    assert "Врачей" not in text
    assert "Комментарий" not in text
    assert "Заявитель" in text
