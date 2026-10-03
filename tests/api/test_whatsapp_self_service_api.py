"""API-тесты: клиника сама подключает свой номер WhatsApp (Embedded Signup Meta).

Проверяется то, ради чего фича существует, и то, где она может навредить:

* номер из приложения WhatsApp Business подключается без регистрации, новый —
  регистрируется с PIN; Skrezo подписывается на вебхуки WABA клиники;
* токен клиники хранится только зашифрованным, и ВСЯ отправка по её номеру идёт
  им, а не общим токеном Skrezo;
* ошибки Meta превращаются в понятные коды, и ничего не сохраняется наполовину;
* номер одной клиники не уезжает к другой; подключать может только владелец;
* архив из приложения не смешивается с живой лентой и не делает диалог «новым»;
  адресная книга даёт имена в списке; отзыв доступа помечает номер;
* ежечасный монитор помечает номер только при явном отказе Meta, пишет команде
  один раз и дозапрашивает синхронизацию в пределах суток.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.main import app
from app.modules.whatsapp.application.services.connection_monitor import (
    WhatsAppConnectionMonitor,
)
from app.modules.whatsapp.presentation.deps import get_onboarding_client, get_whatsapp_client
from app.shared.crypto import secret_box
from app.shared.db.enums import AiConversationStatus
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.audit_log import AuditLog
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.whatsapp_cloud import (
    DownloadedMedia,
    MetaOnboardingError,
    PhoneNumberInfo,
    SentMessage,
    TemplateInfo,
)
from tests.conftest import API, Resources, otp_login

APP_SECRET = "meta-app-secret"


def _digits(n: int = 15) -> str:
    return str(uuid.uuid4().int)[:n]


def _number(*, biz: bool = True, cloud: bool = True) -> PhoneNumberInfo:
    """Номер WABA клиники в форме ответа Graph API."""
    local = _digits(6)
    return PhoneNumberInfo(
        id=_digits(),
        display_phone=f"+996 700 {local[:3]}-{local[3:]}",
        verified_name="Стоматология Улыбка",
        quality_rating="GREEN",
        platform_type="CLOUD_API" if cloud else "NOT_APPLICABLE",
        code_verification_status="VERIFIED",
        is_on_biz_app=biz,
    )


@dataclass
class FakeMeta:
    """Graph API шагов подключения: отвечает заданным и копит вызовы."""

    numbers: list[PhoneNumberInfo]
    token: str = field(default_factory=lambda: f"EAAG-{_digits()}")
    exchange_error: MetaOnboardingError | None = None
    subscribe_error: MetaOnboardingError | None = None
    number_error: MetaOnboardingError | None = None
    info: PhoneNumberInfo | None = None
    codes: list[str] = field(default_factory=list)
    subscribed: list[tuple[str, str]] = field(default_factory=list)
    unsubscribed: list[tuple[str, str]] = field(default_factory=list)
    registered: list[tuple[str, str, str]] = field(default_factory=list)
    syncs: list[tuple[str, str, str]] = field(default_factory=list)

    async def exchange_code(self, code: str) -> str:
        self.codes.append(code)
        if self.exchange_error is not None:
            raise self.exchange_error
        return self.token

    async def phone_numbers(self, waba_id: str, token: str) -> list[PhoneNumberInfo]:
        assert token == self.token
        return list(self.numbers)

    async def phone_number(self, phone_number_id: str, token: str) -> PhoneNumberInfo:
        if self.number_error is not None:
            raise self.number_error
        return self.info or next(n for n in self.numbers if n.id == phone_number_id)

    async def register_number(self, phone_number_id: str, token: str, pin: str) -> None:
        self.registered.append((phone_number_id, token, pin))

    async def subscribe_app(self, waba_id: str, token: str) -> None:
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.subscribed.append((waba_id, token))

    async def unsubscribe_app(self, waba_id: str, token: str) -> None:
        self.unsubscribed.append((waba_id, token))

    async def request_sync(self, phone_number_id: str, token: str, sync_type: str) -> None:
        self.syncs.append((phone_number_id, token, sync_type))


class RecordingWa:
    """Транспорт Cloud API: пишет, каким токеном и что отправлено."""

    def __init__(self, token: str | None = None, log: list[tuple[Any, ...]] | None = None):
        self.token = token
        self.log: list[tuple[Any, ...]] = log if log is not None else []

    def for_token(self, access_token: str | None) -> RecordingWa:
        return RecordingWa(access_token, self.log) if access_token else self

    async def send_text(self, phone_number_id: str, to_phone: str, body: str) -> SentMessage:
        self.log.append(("text", self.token, phone_number_id, body))
        return SentMessage(wa_message_id=f"wamid.out-{_digits()}")

    async def send_template(self, phone_number_id: str, *args: Any, **kw: Any) -> SentMessage:
        self.log.append(("template", self.token, phone_number_id))
        return SentMessage(wa_message_id=f"wamid.tpl-{_digits()}")

    async def send_confirm_button(self, phone_number_id: str, *args: Any) -> SentMessage:
        return SentMessage(wa_message_id=f"wamid.btn-{_digits()}")

    async def fetch_media(self, media_id: str) -> DownloadedMedia | None:
        return None

    async def check_token(self, phone_number_id: str) -> bool:
        return True

    async def fetch_templates(self, waba_id: str) -> list[TemplateInfo] | None:
        self.log.append(("templates", self.token, waba_id))
        return []


@dataclass
class Clinic:
    clinic_id: str
    headers: dict[str, str]


async def _clinic(admin_client: AsyncClient, client: AsyncClient) -> Clinic:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = f"0555{_digits(6)}"
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Номера",
            "adminPhone": phone,
            "name": f"Клиника {_digits(4)}",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    return Clinic(str(created.json()["id"]), await otp_login(client, phone))


@pytest_asyncio.fixture
async def meta(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[FakeMeta, RecordingWa]]:
    """Приложение Meta и ключ шифрования настроены; Graph API подменён."""
    monkeypatch.setattr(settings, "wa_cloud_app_id", "4438887573096444")
    monkeypatch.setattr(settings, "wa_cloud_app_secret", APP_SECRET)
    monkeypatch.setattr(settings, "wa_embedded_signup_config_id", "cfg-1")
    monkeypatch.setattr(settings, "data_encryption_key", Fernet.generate_key().decode())
    fake = FakeMeta(numbers=[_number()])
    wa = RecordingWa()
    app.dependency_overrides[get_onboarding_client] = lambda: fake
    app.dependency_overrides[get_whatsapp_client] = lambda: wa
    yield fake, wa
    app.dependency_overrides.pop(get_onboarding_client, None)
    app.dependency_overrides.pop(get_whatsapp_client, None)


async def _connect(client: AsyncClient, clinic: Clinic, **body: Any) -> Any:
    payload = {"code": f"code-{_digits(6)}", "wabaId": "102290129340398", **body}
    return await client.post(
        f"{API}/clinic/whatsapp/embedded-signup", headers=clinic.headers, json=payload
    )


async def _webhook(client: AsyncClient, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload).encode()
    signature = hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    resp = await client.post(
        f"{API}/whatsapp/webhook",
        content=raw,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={signature}"},
    )
    assert resp.status_code == 200, resp.text


def _change(
    field_name: str, value: dict[str, Any], waba: str = "102290129340398"
) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": waba, "changes": [{"field": field_name, "value": value}]}],
    }


def _meta(number: PhoneNumberInfo) -> dict[str, str]:
    return {"display_phone_number": number.display_phone, "phone_number_id": number.id}


async def _inbound(client: AsyncClient, number: PhoneNumberInfo, patient: str, text: str) -> None:
    await _webhook(
        client,
        _change(
            "messages",
            {
                "messaging_product": "whatsapp",
                "metadata": _meta(number),
                "messages": [
                    {
                        "from": patient,
                        "id": f"wamid.in-{_digits()}",
                        "timestamp": str(int(time.time())),
                        "type": "text",
                        "text": {"body": text},
                    }
                ],
            },
        ),
    )


async def _conversations(client: AsyncClient, clinic: Clinic) -> list[dict[str, Any]]:
    resp = await client.get(f"{API}/clinic/conversations", headers=clinic.headers)
    assert resp.status_code == 200, resp.text
    items: list[dict[str, Any]] = resp.json()["list"]
    return items


async def _binding(res: Resources, clinic: Clinic) -> ClinicWhatsApp:
    async with res.factory() as session:
        binding = (
            await session.execute(
                select(ClinicWhatsApp).where(
                    ClinicWhatsApp.clinic_id == uuid.UUID(clinic.clinic_id)
                )
            )
        ).scalar_one()
        return binding


# ── 1. Без настроек Meta кнопки нет ─────────────────────────────────────────


async def test_without_meta_config_signup_is_unavailable(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    clinic = await _clinic(admin_client, client)
    status = await client.get(f"{API}/clinic/whatsapp", headers=clinic.headers)
    assert status.status_code == 200, status.text
    assert status.json()["connected"] is False
    assert status.json()["signup"]["available"] is False

    resp = await _connect(client, clinic)
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"] == "whatsapp_signup_unavailable"


# ── 2. Номер из приложения: подключение и отправка токеном клиники ──────────


async def test_app_number_connects_and_all_sending_uses_clinic_token(
    admin_client: AsyncClient,
    client: AsyncClient,
    meta: tuple[FakeMeta, RecordingWa],
    _resources: Resources,
) -> None:
    fake, wa = meta
    number = fake.numbers[0]
    clinic = await _clinic(admin_client, client)

    status = (await client.get(f"{API}/clinic/whatsapp", headers=clinic.headers)).json()
    assert status["signup"] == {
        "available": True,
        "appId": "4438887573096444",
        "configId": "cfg-1",
        "graphApiVersion": "v23.0",
        "featureType": "whatsapp_business_app_onboarding",
        "sessionInfoVersion": "3",
    }

    resp = await _connect(client, clinic)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connected"] is True
    assert body["phoneNumberId"] == number.id
    assert (
        body["displayPhone"] == "+996" + "".join(c for c in number.display_phone if c.isdigit())[3:]
    )
    assert body["mode"] == "coexistence" and body["connectedVia"] == "embedded_signup"
    assert body["verifiedName"] == "Стоматология Улыбка" and body["qualityRating"] == "GREEN"
    assert body["sync"]["contactsRequestedAt"] and body["sync"]["historyRequestedAt"]
    # Номер из приложения уже зарегистрирован — регистрации нет; подписка — есть.
    assert fake.registered == []
    assert fake.subscribed == [("102290129340398", fake.token)]
    assert {s[2] for s in fake.syncs} == {"smb_app_state_sync", "history"}
    # Шаблоны WABA клиники спрошены её токеном.
    assert ("templates", fake.token, "102290129340398") in wa.log

    # Токен в базе только зашифрованным.
    binding = await _binding(_resources, clinic)
    assert binding.access_token_enc and fake.token not in binding.access_token_enc
    assert secret_box().decrypt(binding.access_token_enc) == fake.token

    # Ответ пациенту из кабинета уходит токеном клиники.
    await _inbound(client, number, "996555123456", "Здравствуйте")
    conversation = (await _conversations(client, clinic))[0]
    sent = await client.post(
        f"{API}/clinic/conversations/{conversation['id']}/messages",
        headers=clinic.headers,
        json={"body": "Добрый день!"},
    )
    assert sent.status_code in (200, 201), sent.text
    assert ("text", fake.token, number.id, "Добрый день!") in wa.log

    async with _resources.factory() as session:
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.clinic_id == uuid.UUID(clinic.clinic_id),
                    AuditLog.action == "whatsapp.connected",
                )
            )
        ).scalar_one()
        assert audit.payload is not None and audit.payload["phoneNumberId"] == number.id


# ── 3. Новый номер регистрируется с PIN ─────────────────────────────────────


async def test_new_number_is_registered_with_pin_and_needs_no_app_sync(
    admin_client: AsyncClient,
    client: AsyncClient,
    meta: tuple[FakeMeta, RecordingWa],
    _resources: Resources,
) -> None:
    fake, _ = meta
    number = _number(biz=False, cloud=False)
    fake.numbers = [number]
    clinic = await _clinic(admin_client, client)

    resp = await _connect(client, clinic, phoneNumberId=number.id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["mode"] == "api_only"
    assert resp.json()["sync"]["contactsRequestedAt"] is None
    assert fake.syncs == []
    [(registered_id, token, pin)] = fake.registered
    assert registered_id == number.id and token == fake.token
    assert len(pin) == 6 and pin.isdigit()
    binding = await _binding(_resources, clinic)
    assert binding.two_step_pin_enc and secret_box().decrypt(binding.two_step_pin_enc) == pin


# ── 4. Выбор номера в WABA ──────────────────────────────────────────────────


async def test_number_must_be_chosen_when_waba_has_several(
    admin_client: AsyncClient, client: AsyncClient, meta: tuple[FakeMeta, RecordingWa]
) -> None:
    fake, _ = meta
    first, second = _number(), _number()
    fake.numbers = [first, second]
    clinic = await _clinic(admin_client, client)

    resp = await _connect(client, clinic)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "phone_number_required"
    assert {n["id"] for n in resp.json()["phoneNumbers"]} == {first.id, second.id}

    wrong = await _connect(client, clinic, phoneNumberId=_digits())
    assert wrong.status_code == 400 and wrong.json()["error"] == "phone_number_not_in_waba"

    ok = await _connect(client, clinic, phoneNumberId=second.id)
    assert ok.status_code == 200, ok.text
    assert ok.json()["phoneNumberId"] == second.id


# ── 5. Ошибки Meta: понятные коды, ничего не сохранено ─────────────────────


async def test_meta_failures_map_to_codes_and_save_nothing(
    admin_client: AsyncClient, client: AsyncClient, meta: tuple[FakeMeta, RecordingWa]
) -> None:
    fake, _ = meta
    clinic = await _clinic(admin_client, client)

    fake.exchange_error = MetaOnboardingError("expired", status=400, meta_code=100)
    expired = await _connect(client, clinic)
    assert expired.status_code == 400 and expired.json()["error"] == "signup_code_invalid"

    fake.exchange_error = MetaOnboardingError("timeout")
    offline = await _connect(client, clinic)
    assert offline.status_code == 502 and offline.json()["error"] == "meta_unavailable"

    fake.exchange_error = None
    fake.subscribe_error = MetaOnboardingError("boom", status=500)
    half = await _connect(client, clinic)
    assert half.status_code == 502, half.text
    assert half.json()["error"] == "meta_step_failed" and half.json()["step"] == "subscribe"

    status = await client.get(f"{API}/clinic/whatsapp", headers=clinic.headers)
    assert status.json()["connected"] is False


# ── 6. Номер одной клиники не уезжает к другой ──────────────────────────────


async def test_number_conflicts_and_reconnect_of_same_number(
    admin_client: AsyncClient,
    client: AsyncClient,
    meta: tuple[FakeMeta, RecordingWa],
    _resources: Resources,
) -> None:
    fake, _ = meta
    number = fake.numbers[0]
    clinic_a = await _clinic(admin_client, client)
    clinic_b = await _clinic(admin_client, client)
    assert (await _connect(client, clinic_a)).status_code == 200

    taken = await _connect(client, clinic_b)
    assert taken.status_code == 409 and taken.json()["error"] == "phone_number_taken"

    fake.numbers = [_number()]
    other = await _connect(client, clinic_a)
    assert other.status_code == 409 and other.json()["error"] == "whatsapp_already_connected"

    # Переподключение того же номера (например, после отзыва доступа) — новый токен.
    fake.numbers = [number]
    fake.token = "EAAG-renewed"
    again = await _connect(client, clinic_a)
    assert again.status_code == 200, again.text
    binding = await _binding(_resources, clinic_a)
    assert binding.access_token_enc is not None
    assert secret_box().decrypt(binding.access_token_enc) == "EAAG-renewed"


# ── 7. Права: подключает владелец, смотрит и администратор переписки ────────


async def test_only_owner_manages_number_manager_can_view(
    admin_client: AsyncClient, client: AsyncClient, meta: tuple[FakeMeta, RecordingWa]
) -> None:
    clinic = await _clinic(admin_client, client)
    roles = (await client.get(f"{API}/clinic/roles", headers=clinic.headers)).json()["items"]
    manager_role = next(r["id"] for r in roles if r["key"] == "manager")
    phone = f"0555{_digits(6)}"
    added = await client.post(
        f"{API}/clinic/staff",
        headers=clinic.headers,
        json={"fullName": "Менеджер Ресепшена", "phone": phone, "roleId": manager_role},
    )
    assert added.status_code == 201, added.text
    manager = Clinic(clinic.clinic_id, await otp_login(client, phone))

    seen = await client.get(f"{API}/clinic/whatsapp", headers=manager.headers)
    assert seen.status_code == 200, seen.text
    for resp in (
        await _connect(client, manager),
        await client.post(f"{API}/clinic/whatsapp/refresh", headers=manager.headers),
        await client.delete(f"{API}/clinic/whatsapp", headers=manager.headers),
    ):
        assert resp.status_code == 403, resp.text


# ── 8. Отключение ───────────────────────────────────────────────────────────


async def test_disconnect_unsubscribes_and_keeps_conversations(
    admin_client: AsyncClient,
    client: AsyncClient,
    meta: tuple[FakeMeta, RecordingWa],
    _resources: Resources,
) -> None:
    fake, _ = meta
    number = fake.numbers[0]
    clinic = await _clinic(admin_client, client)
    assert (await _connect(client, clinic)).status_code == 200
    await _inbound(client, number, "996555222333", "Добрый день")

    resp = await client.delete(f"{API}/clinic/whatsapp", headers=clinic.headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["connected"] is False
    assert fake.unsubscribed == [("102290129340398", fake.token)]
    binding = await _binding(_resources, clinic)
    assert binding.active is False and binding.access_token_enc is None
    assert len(await _conversations(client, clinic)) == 1

    again = await client.delete(f"{API}/clinic/whatsapp", headers=clinic.headers)
    assert again.status_code == 404 and again.json()["error"] == "whatsapp_not_connected"


# ── 9. Вебхуки номера из приложения ─────────────────────────────────────────


async def test_history_contacts_and_account_webhooks(
    admin_client: AsyncClient,
    client: AsyncClient,
    meta: tuple[FakeMeta, RecordingWa],
    _resources: Resources,
) -> None:
    fake, _ = meta
    number = fake.numbers[0]
    business = "".join(c for c in number.display_phone if c.isdigit())
    clinic = await _clinic(admin_client, client)
    assert (await _connect(client, clinic)).status_code == 200

    live_patient, archived_patient = "996555300300", "996555400400"
    await _inbound(client, number, live_patient, "Сегодня можно?")
    live = (await _conversations(client, clinic))[0]
    unread_before = live["unreadCount"]

    day = 86400
    now = int(time.time())
    history = {
        "messaging_product": "whatsapp",
        "metadata": _meta(number),
        "history": [
            {
                "metadata": {"phase": 1, "chunk_order": 1, "progress": 100},
                "threads": [
                    {
                        "id": live_patient,
                        "messages": [
                            {
                                "from": live_patient,
                                "id": f"wamid.h-{_digits()}",
                                "timestamp": str(now - 30 * day),
                                "type": "text",
                                "text": {"body": "Прошлый месяц"},
                                "history_context": {"status": "READ"},
                            }
                        ],
                    },
                    {
                        "id": archived_patient,
                        "messages": [
                            {
                                "from": business,
                                "id": f"wamid.h-{_digits()}",
                                "timestamp": str(now - 60 * day),
                                "type": "text",
                                "text": {"body": "Ждём вас в среду"},
                                "history_context": {"status": "DELIVERED"},
                            },
                            {
                                "from": archived_patient,
                                "id": f"wamid.h-{_digits()}",
                                "timestamp": str(now - 59 * day),
                                "type": "media_placeholder",
                                "history_context": {"status": "PLAYED"},
                            },
                        ],
                    },
                ],
            }
        ],
    }
    await _webhook(client, _change("history", history))
    await _webhook(client, _change("history", history))  # Meta повторила пачку

    items = {c["waPhone"]: c for c in await _conversations(client, clinic)}
    assert items["+" + live_patient]["unreadCount"] == unread_before
    archived = items["+" + archived_patient]
    assert archived["status"] == "closed" and archived["unreadCount"] == 0
    assert (
        archived["lastMessagePreview"] == "Ждём вас в среду"
        or archived["lastMessagePreview"] is None
    )  # последнее в архиве — медиа без текста
    async with _resources.factory() as session:
        conv = await session.get(AiConversation, uuid.UUID(archived["id"]))
        assert conv is not None and conv.status is AiConversationStatus.CLOSED
        assert conv.last_msg_at < datetime.now(UTC) - timedelta(days=58)

    page = await client.get(
        f"{API}/clinic/conversations/{archived['id']}/history", headers=clinic.headers
    )
    assert page.status_code == 200, page.text
    rows = page.json()["items"]
    assert [r["direction"] for r in rows] == ["in", "out"]  # от свежих к старым
    assert rows[0]["mediaType"] == "media_placeholder" and rows[1]["status"] == "DELIVERED"

    status = (await client.get(f"{API}/clinic/whatsapp", headers=clinic.headers)).json()
    assert status["sync"]["historyProgress"] == 100 and status["sync"]["historySyncedAt"]

    # Адресная книга: имя в списке, удаление — имя пропадает.
    contact = {"full_name": "Айгуль (чистка)", "first_name": "Айгуль", "phone_number": live_patient}
    await _webhook(
        client,
        _change(
            "smb_app_state_sync",
            {
                "messaging_product": "whatsapp",
                "metadata": _meta(number),
                "state_sync": [{"type": "contact", "contact": contact, "action": "add"}],
            },
        ),
    )
    names = {c["waPhone"]: c["contactName"] for c in await _conversations(client, clinic)}
    assert names["+" + live_patient] == "Айгуль (чистка)"
    await _webhook(
        client,
        _change(
            "smb_app_state_sync",
            {
                "messaging_product": "whatsapp",
                "metadata": _meta(number),
                "state_sync": [{"type": "contact", "contact": contact, "action": "remove"}],
            },
        ),
    )
    names = {c["waPhone"]: c["contactName"] for c in await _conversations(client, clinic)}
    assert names["+" + live_patient] is None

    # Клиника отозвала доступ Skrezo в Meta.
    await _webhook(client, _change("account_update", {"event": "PARTNER_REMOVED"}))
    status = (await client.get(f"{API}/clinic/whatsapp", headers=clinic.headers)).json()
    assert status["needsReconnect"]["reason"] == "partner_removed"
    assert "отозван" in status["needsReconnect"]["message"]


async def test_history_pages_by_time_not_by_arrival(
    admin_client: AsyncClient, client: AsyncClient, meta: tuple[FakeMeta, RecordingWa]
) -> None:
    fake, _ = meta
    number = fake.numbers[0]
    clinic = await _clinic(admin_client, client)
    assert (await _connect(client, clinic)).status_code == 200
    patient = "996555500500"
    now = int(time.time())

    def chunk(days_ago: list[int]) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "metadata": _meta(number),
            "history": [
                {
                    "metadata": {"progress": 50},
                    "threads": [
                        {
                            "id": patient,
                            "messages": [
                                {
                                    "from": patient,
                                    "id": f"wamid.p-{_digits()}",
                                    "timestamp": str(now - d * 86400),
                                    "type": "text",
                                    "text": {"body": f"{d} дн."},
                                }
                                for d in days_ago
                            ],
                        }
                    ],
                }
            ],
        }

    # Сначала свежие дни, потом старые — как присылает Meta.
    await _webhook(client, _change("history", chunk([1, 2])))
    await _webhook(client, _change("history", chunk([90, 3, 120])))
    conversation = (await _conversations(client, clinic))[0]

    seen: list[str] = []
    before: int | None = None
    while True:
        params: dict[str, Any] = {"limit": 2}
        if before is not None:
            params["beforeId"] = before
        page = (
            await client.get(
                f"{API}/clinic/conversations/{conversation['id']}/history",
                headers=clinic.headers,
                params=params,
            )
        ).json()
        seen.extend(r["body"] for r in page["items"])
        before = page["nextBeforeId"]
        if before is None:
            break
    assert seen == ["1 дн.", "2 дн.", "3 дн.", "90 дн.", "120 дн."]


# ── 10. Обновление состояния ────────────────────────────────────────────────


async def test_refresh_updates_number_and_detects_revoked_access(
    admin_client: AsyncClient, client: AsyncClient, meta: tuple[FakeMeta, RecordingWa]
) -> None:
    fake, _ = meta
    number = fake.numbers[0]
    clinic = await _clinic(admin_client, client)
    assert (await _connect(client, clinic)).status_code == 200

    fake.info = PhoneNumberInfo(
        id=number.id,
        display_phone=number.display_phone,
        verified_name="Улыбка+",
        quality_rating="YELLOW",
        platform_type="CLOUD_API",
        code_verification_status="VERIFIED",
        is_on_biz_app=True,
    )
    fresh = await client.post(f"{API}/clinic/whatsapp/refresh", headers=clinic.headers)
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["qualityRating"] == "YELLOW" and fresh.json()["verifiedName"] == "Улыбка+"

    fake.number_error = MetaOnboardingError("revoked", status=401, meta_code=190)
    revoked = await client.post(f"{API}/clinic/whatsapp/refresh", headers=clinic.headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["needsReconnect"]["reason"] == "token_invalid"

    fake.number_error = None
    healed = await client.post(f"{API}/clinic/whatsapp/refresh", headers=clinic.headers)
    assert healed.json()["needsReconnect"] is None


# ── 11. Ежечасный монитор ───────────────────────────────────────────────────


class _TeamChat:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def notify_team(self, text: str, *, event: str) -> None:
        self.messages.append(text)


async def test_monitor_flags_on_denial_only_notifies_once_and_retries_sync(
    admin_client: AsyncClient,
    client: AsyncClient,
    meta: tuple[FakeMeta, RecordingWa],
    _resources: Resources,
) -> None:
    fake, _ = meta
    number = fake.numbers[0]
    clinic = await _clinic(admin_client, client)
    assert (await _connect(client, clinic)).status_code == 200
    team = _TeamChat()

    async with _resources.factory() as session:
        binding = (
            await session.execute(
                select(ClinicWhatsApp).where(ClinicWhatsApp.phone_number_id == number.id)
            )
        ).scalar_one()
        # Синхронизация истории не прошла при подключении — монитор её дозапросит.
        binding.history_sync_requested_at = None
        await session.commit()

        monitor = WhatsAppConnectionMonitor(session, fake, team)  # type: ignore[arg-type]
        fake.syncs.clear()
        # Сетевой сбой — номер не помечается.
        fake.number_error = MetaOnboardingError("timeout")
        report = await monitor.run([number.id])
        assert report.flagged == 0 and team.messages == []
        assert [s[2] for s in fake.syncs] == ["history"] and report.syncs == 1

        # Явный отказ Meta — помечается, команде одно сообщение.
        fake.number_error = MetaOnboardingError("denied", status=403)
        report = await monitor.run([number.id])
        assert report.flagged == 1 and report.notified == 1
        assert len(team.messages) == 1 and number.id not in team.messages[0]
        assert "переподключить" in team.messages[0]
        again = await monitor.run([number.id])
        assert again.notified == 0 and len(team.messages) == 1

        # Токен снова принимается — отметка снимается сама.
        healed = await monitor.run([])
        assert healed.cleared == 1

        # Окно синхронизации — сутки: позже ничего не запрашивается.
        await session.refresh(binding)
        binding.connected_at = datetime.now(UTC) - timedelta(hours=25)
        binding.contacts_sync_requested_at = None
        await session.commit()
        fake.syncs.clear()
        late = await monitor.run([])
        assert late.syncs == 0 and fake.syncs == []
