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

import html as html_lib
import itertools
import json
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from app.core.jobs import JobQueue
from app.main import app
from app.modules.clients.domain.models import ClientCard
from app.modules.clients.domain.team_messages import (
    card_buttons,
    client_registered_text,
    trial_expired_text,
)
from app.modules.clinic_applications.domain.messages import new_application_text
from app.modules.clinic_applications.domain.models import ApplicationRow
from app.shared.db.enums import ClinicApplicationStatus, ClinicType, DoctorsCountRange
from app.shared.db.models.clinic_application import ClinicApplication
from app.shared.notifications.channels import ChatButton, MessageDeliveryError, TextChannel
from app.shared.notifications.delivery import TextDeliveryService
from app.shared.notifications.deps import get_notifier
from app.shared.notifications.notifier import Notifier
from app.shared.notifications.smspro import SmsProTextChannel
from app.shared.notifications.telegram_chat import TelegramChatChannel
from app.shared.notifications.whatsapp import WhatsAppTextChannel
from tests.conftest import API, Resources, otp_code, otp_login

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
    """Чат-«записная книжка»: текст, флаг HTML и кнопки каждого сообщения."""

    name = "chat-recorder"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.html: list[bool] = []
        self.buttons: list[tuple[ChatButton, ...]] = []
        self._fail = fail

    async def send(
        self, text: str, *, html: bool = False, buttons: Sequence[ChatButton] = ()
    ) -> None:
        self.sent.append(text)
        self.html.append(html)
        self.buttons.append(tuple(buttons))
        if self._fail:
            raise MessageDeliveryError("chat: сломан")


def _plain(text: str) -> str:
    """HTML-карточка глазами читателя: без тегов, сущности — обратно в символы."""
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text))


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
    assert "parse_mode" not in body  # простой текст по умолчанию
    assert "reply_markup" not in body


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


def _telegram(client: httpx.AsyncClient) -> TelegramChatChannel:
    return TelegramChatChannel(
        bot_token="123:abc",
        chat_id="-100500",
        timeout=1.0,
        api_url="https://telegram.example",
        client=client,
    )


async def test_telegram_chat_sends_html_card_with_buttons() -> None:
    """Карточка: parse_mode=HTML, кнопки-ссылки одним рядом, превью ссылок выключено."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as client:
        await _telegram(client).send(
            "<b>Карточка</b>", html=True, buttons=[("💬 WhatsApp", "https://wa.me/996700123456")]
        )

    body = seen["body"]
    assert body["parse_mode"] == "HTML"
    assert body["text"] == "<b>Карточка</b>"
    assert body["reply_markup"] == {
        "inline_keyboard": [[{"text": "💬 WhatsApp", "url": "https://wa.me/996700123456"}]]
    }
    assert body["link_preview_options"] == {"is_disabled": True}


async def test_telegram_chat_falls_back_to_plain_text_when_markup_rejected() -> None:
    """400 на разметку — сразу повтор простым текстом без кнопок: карточка не теряется."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "parse_mode" in body:
            return httpx.Response(
                400, json={"ok": False, "description": "Bad Request: can't parse entities"}
            )
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as client:
        await _telegram(client).send(
            "<b>Клиника:</b> Dent &amp; Co &lt;1&gt;",
            html=True,
            buttons=[("💬 WhatsApp", "https://wa.me/996700123456")],
        )

    assert len(bodies) == 2
    retry = bodies[1]
    assert "parse_mode" not in retry
    assert "reply_markup" not in retry
    assert retry["text"] == "Клиника: Dent & Co <1>"


async def test_telegram_chat_rejected_plain_text_is_delivery_error() -> None:
    """400 на простой текст повторять нечем — это недоставка (задача уйдёт в ретрай)."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"ok": False, "description": "chat not found"})

    async with _client(handler) as client:
        with pytest.raises(MessageDeliveryError, match="chat not found"):
            await _telegram(client).send(TEXT)

    assert len(calls) == 1


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


async def test_notifier_passes_markup_and_buttons_to_team_chat() -> None:
    """Без очереди карточка уходит на месте — с разметкой и кнопками, как поставлена."""
    chat = _ChatRecorder()

    await Notifier(JobQueue(None), TextDeliveryService([_Recorder()]), chat).notify_team(
        "<b>карточка</b>", event="test", html=True, buttons=[("WhatsApp", "https://wa.me/1")]
    )

    assert chat.sent == ["<b>карточка</b>"]
    assert chat.html == [True]
    assert chat.buttons == [(("WhatsApp", "https://wa.me/1"),)]


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
    # Карточка с разметкой и кнопками «написать» на нормализованный номер.
    assert chat.html == [True]
    (buttons,) = chat.buttons
    assert [label for label, _ in buttons] == ["💬 WhatsApp", "✈️ Telegram"]
    assert buttons[0][1].startswith("https://wa.me/99670094")


async def test_landing_lead_without_clinic_name_reaches_team_chat(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Лид «имя + телефон» (ровно как с лендинга) доходит до чата и не ломает текст.

    Названия клиники нет — строки «Клиника:» в сообщении быть не должно, а также
    ни «None», ни пустых значений; телефон помечен неподтверждённым.
    """
    phones, chat = _Recorder(), _ChatRecorder()
    _override_notifier(phones, chat)

    created = await public_client.post(
        f"{API}/leads", json={"fullName": "Тест Тестов", "phone": "0555 000 001"}
    )

    assert created.status_code == 201, created.text
    assert phones.sent == []
    (text,) = chat.sent
    assert "Тест Тестов" in text
    assert "+996555000001" in text  # нормализованный номер, а не «как ввели»
    assert "не подтверждён" in text
    assert "Клиника" not in text
    assert "Врачей" not in text
    assert "Источник" not in text
    assert "Комментарий" not in text
    assert "None" not in text


async def test_team_message_about_lead_carries_source_and_optional_fields(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """В сообщении команде есть source (откуда пришёл человек) и всё, что он написал."""
    phones, chat = _Recorder(), _ChatRecorder()
    _override_notifier(phones, chat)

    created = await public_client.post(
        f"{API}/leads",
        json={
            "fullName": "Айгүл",
            "phone": _phone(),
            "clinicName": "Дента+",
            "doctorsCountRange": "6-10",
            "comment": "Позвоните после 14:00",
            "source": "instagram",
        },
    )

    assert created.status_code == 201, created.text
    (text,) = chat.sent
    plain = _plain(text)
    assert "Источник: instagram" in plain
    assert "Клиника: Дента+" in plain
    assert "Врачей: 6-10" in plain
    assert "Комментарий:\nПозвоните после 14:00" in plain
    assert "не подтверждён" in plain
    assert "#заявка #сайт" in plain


async def test_duplicate_lead_does_not_ping_team_again(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Отказ по дедупу (409) — не событие: команду второй раз не тревожим."""
    phones, chat = _Recorder(), _ChatRecorder()
    _override_notifier(phones, chat)
    phone = _phone()

    first = await public_client.post(f"{API}/leads", json={"fullName": "Первый", "phone": phone})
    second = await public_client.post(f"{API}/leads", json={"fullName": "Повтор", "phone": phone})

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    assert len(chat.sent) == 1


async def test_rejected_lead_does_not_ping_team(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Невалидный лид (422) ничего не записывает и команде не пишет."""
    phones, chat = _Recorder(), _ChatRecorder()
    _override_notifier(phones, chat)

    bad = await public_client.post(f"{API}/leads", json={"fullName": "Кривой", "phone": "123"})

    assert bad.status_code == 422, bad.text
    assert chat.sent == []


async def test_otp_application_pings_team_chat_with_city_and_verified_phone(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Заявка из приложения (через OTP) тоже приходит в канал — с городом и ✅ номером."""
    phones, chat = _Recorder(), _ChatRecorder()
    _override_notifier(phones, chat)
    phone = _phone()

    requested = await public_client.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text
    verified = await public_client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await otp_code(phone)}
    )
    assert verified.status_code == 200, verified.text
    city = (await public_client.get(f"{API}/cities")).json()[0]
    submitted = await public_client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": verified.json()["registrationToken"],
            "companyName": "Через OTP",
            "cityId": city["id"],
            "doctorsCount": "2-5",
        },
    )

    assert submitted.status_code == 201, submitted.text
    assert phones.sent == []
    (text,) = chat.sent
    plain = _plain(text)
    assert "Новая заявка из приложения" in plain
    assert "Клиника: Через OTP" in plain
    assert f"Город: {city['name']}" in plain
    assert "Врачей: 2-5" in plain
    assert "подтверждён кодом" in plain
    assert "не подтверждён" not in plain
    assert "#заявка #приложение" in plain
    assert chat.buttons[0]  # кнопки «написать» на подтверждённый номер


async def test_lead_is_saved_even_when_team_chat_is_down(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Чат команды лежит — лид всё равно принят (201) и записан в очередь."""
    phones, chat = _Recorder(fail=True), _ChatRecorder(fail=True)
    _override_notifier(phones, chat)

    created = await public_client.post(
        f"{API}/leads", json={"fullName": "Переживёт сбой", "phone": _phone()}
    )

    assert created.status_code == 201, created.text
    assert len(chat.sent) == 1  # попытка была
    async with _resources.factory() as session:
        saved = await session.get(ClinicApplication, uuid.UUID(created.json()["id"]))
    assert saved is not None


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


# ── Карточка заявки ─────────────────────────────────────────────────────────


def _lead_row(**overrides: Any) -> ApplicationRow:
    """Лид с сайта: только имя и телефон; остальное — через ``overrides``."""
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "company_name": None,
        "city_id": None,
        "city_name": None,
        "doctors_count": None,
        "full_name": "Заявитель",
        "phone": "+996700123456",
        "phone_verified": False,
        "source": None,
        "comment": None,
        "status": ClinicApplicationStatus.PENDING,
        "converted_clinic_id": None,
        "reviewed_by_account_id": None,
        "reviewed_by_name": None,
        "reviewed_at": None,
        "created_at": datetime(2026, 10, 11, 8, 32, tzinfo=UTC),
    }
    return ApplicationRow(**{**fields, **overrides})


def test_lead_card_lists_optional_fields_only_when_filled() -> None:
    """Пустые поля лида в карточку не лезут — иначе в канале строки-прочерки."""
    text = _plain(new_application_text(_lead_row(company_name="Клиника")))

    assert "Клиника: Клиника" in text
    assert "Врачей" not in text
    assert "Комментарий" not in text
    assert "Источник" not in text
    assert "Город" not in text
    assert "None" not in text


def test_lead_card_marks_phone_unverified_and_shows_bishkek_time() -> None:
    """Неподтверждённый номер виден в строке телефона; время — по Бишкеку, не UTC."""
    text = _plain(new_application_text(_lead_row()))

    assert "Новая заявка с сайта" in text
    assert "Контакт: Заявитель" in text
    assert "Телефон: +996700123456 · ⚠️ не подтверждён" in text
    assert "🕒 11.10.2026 14:32" in text  # 08:32 UTC = 14:32 в Бишкеке
    assert "#заявка #сайт" in text


def test_lead_card_renders_doctors_range_as_plain_value() -> None:
    """Диапазон врачей — «6-10», а не repr enum («DoctorsCountRange.FROM_6_TO_10»)."""
    text = _plain(new_application_text(_lead_row(doctors_count=DoctorsCountRange.FROM_6_TO_10)))

    assert "Врачей: 6-10" in text
    assert "DoctorsCountRange" not in text


def test_card_escapes_user_input_so_markup_cannot_break() -> None:
    """``<``, ``&`` и теги из формы экранируются: разметка остаётся только нашей.

    Иначе Bot API отклонил бы сообщение целиком — и заявка осталась бы незамеченной.
    """
    raw = new_application_text(
        _lead_row(company_name="Dent_<b>&Co", comment="a_b *c* <i>x</i>", source="utm_source=ig")
    )

    assert "Dent_&lt;b&gt;&amp;Co" in raw
    assert "&lt;i&gt;x&lt;/i&gt;" in raw
    # Читатель видит ровно то, что ввели.
    plain = _plain(raw)
    assert "Клиника: Dent_<b>&Co" in plain
    assert "a_b *c* <i>x</i>" in plain
    assert "Источник: utm_source=ig" in plain


def test_long_comment_is_trimmed_to_fit_telegram_limit() -> None:
    """Комментарий обрезается: у Telegram потолок 4096 символов на сообщение."""
    text = new_application_text(_lead_row(comment="я" * 5000))

    assert len(text) < 4096
    assert "…" in text


# ── Карточка клиента: регистрация и конец триала ────────────────────────────


def _card(**overrides: Any) -> ClientCard:
    """Клиника с владельцем, сотрудниками и приёмами; правки — через ``overrides``."""
    fields: dict[str, Any] = {
        "clinic_id": uuid.uuid4(),
        "name": "Дент-Люкс",
        "type": ClinicType.CLINIC,
        "city_name": "Бишкек",
        "address": "ул. Ленина, 1",
        "business_phone": "+996312000000",
        "owner_full_name": "Иван Иванов",
        "owner_phone": "+996700123456",
        "owner_specialization": "Ортодонт",
        "registered_at": datetime(2026, 9, 11, 6, 0, tzinfo=UTC),
        "trial_started_at": datetime(2026, 9, 11, 6, 0, tzinfo=UTC),
        "trial_ends_at": datetime(2026, 10, 11, 6, 0, tzinfo=UTC),
        "staff_count": 3,
        "patients_count": 45,
        "appointments_count": 121,
    }
    return ClientCard(**{**fields, **overrides})


def test_trial_expired_card_tells_who_it_is_and_how_to_reach() -> None:
    """Карточка конца триала: кто, где, телефоны, сроки, активность, кнопки."""
    card = _card()
    text = _plain(trial_expired_text(card))

    assert "Закончился пробный период" in text
    assert "Клиника: Дент-Люкс" in text
    assert "Владелец: Иван Иванов" in text
    assert "Специализация: Ортодонт" in text
    assert "Телефон: +996700123456" in text
    assert "Телефон клиники: +996312000000" in text
    assert "Адрес: Бишкек, ул. Ленина, 1" in text
    assert "Триал: 11.09.2026 — 11.10.2026" in text
    assert "Активность: 3 сотрудника · 45 пациентов · 121 приём" in text
    assert "#триал_закончился #клиника" in text
    assert card_buttons(card) == (
        ("💬 WhatsApp", "https://wa.me/996700123456"),
        ("✈️ Telegram", "https://t.me/+996700123456"),
    )


def test_trial_expired_card_flags_clinic_that_never_worked() -> None:
    """Ноль приёмов — главный сигнал для звонка, он назван словами."""
    text = _plain(trial_expired_text(_card(patients_count=0, appointments_count=0)))

    assert "ни одного приёма" in text


def test_solo_card_names_the_doctor_and_skips_staff_and_duplicate_phone() -> None:
    """Соло: «Соло-врач», без счётчика сотрудников и без второго такого же номера."""
    card = _card(
        type=ClinicType.SOLO,
        name="Иван Иванов",
        business_phone="+996700123456",
        address=None,
        staff_count=1,
    )
    text = _plain(trial_expired_text(card))

    assert "Соло-врач: Иван Иванов" in text
    assert "Клиника:" not in text
    assert "Телефон клиники" not in text
    assert "сотрудник" not in text
    assert "Адрес: Бишкек" in text
    assert "#соло" in text


def test_card_without_owner_has_no_buttons_and_says_so() -> None:
    """Владельца нет — писать некому: без кнопок, с пометкой."""
    card = _card(owner_full_name=None, owner_phone=None, owner_specialization=None)
    text = _plain(trial_expired_text(card))

    assert "нет активного владельца" in text
    assert "None" not in text
    assert card_buttons(card) == ()


def test_registered_card_shows_trial_end() -> None:
    """Карточка регистрации: кто пришёл и до какого числа у него триал."""
    text = _plain(client_registered_text(_card(type=ClinicType.SOLO, name="Иван Иванов")))

    assert "Новая регистрация: соло-врач" in text
    assert "Соло-врач: Иван Иванов" in text
    assert "Телефон подтверждён кодом" in text
    assert "Пробный период: до 11.10.2026" in text
    assert "#регистрация #соло" in text


async def test_solo_registration_sends_card_to_team_chat(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Соло-врач зарегистрировался сам — команде карточка с именем, городом и кнопками."""
    phones, chat = _Recorder(), _ChatRecorder()
    _override_notifier(phones, chat)
    phone = _phone()

    requested = await public_client.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text
    verified = await public_client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await otp_code(phone)}
    )
    assert verified.status_code == 200, verified.text
    spec = (await public_client.get(f"{API}/specializations")).json()[0]
    city = (await public_client.get(f"{API}/cities")).json()[0]
    registered = await public_client.post(
        f"{API}/auth/solo/register",
        json={
            "registrationToken": verified.json()["registrationToken"],
            "fullName": "Соло Регистрации",
            "specializationId": spec["id"],
            "cityId": city["id"],
        },
    )

    assert registered.status_code == 201, registered.text
    (text,) = chat.sent
    plain = _plain(text)
    assert "Новая регистрация: соло-врач" in plain
    assert "Соло-врач: Соло Регистрации" in plain
    assert f"Специализация: {spec['name']}" in plain
    assert f"Адрес: {city['name']}" in plain
    assert "Пробный период: до" in plain
    assert chat.html == [True]
    assert chat.buttons[0][0][1].startswith("https://wa.me/99670094")
