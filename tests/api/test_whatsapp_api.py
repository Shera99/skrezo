"""API-тесты WhatsApp-канала (Ф1): вебхук, диалоги, окно 24 часов.

Канал — транспорт без интеллекта, и проверяется именно транспортная дисциплина:

* вебхук отвечает 200 и сохраняет сообщение; повторная доставка от Meta не
  создаёт дубль (``wa_message_id`` уникален);
* подпись ``X-Hub-Signature-256`` обязательна, когда задан APP_SECRET, — чужой
  POST не может подложить «сообщение пациента»;
* неизвестный ``phone_number_id`` молча игнорируется (Meta получает 200);
* пациент матчится по номеру с глобальной карточкой;
* свободный текст доступен только в 24-часовое окно после сообщения пациента;
* клиника не видит диалогов соседней (изоляция), врач без права — переписку.

Очередь в тестах недоступна — вебхук обрабатывает payload на месте (это
штатный фолбэк, а не тестовый обход).
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import time
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.shared.db.enums import AiConversationStatus
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.whatsapp_template import WhatsAppTemplate
from tests.conftest import API, Resources, otp_login, own_membership_id

_phone_seq = itertools.count(1)
_wa_seq = itertools.count(1)
_pnid_seq = itertools.count(1)


def _phone() -> str:
    return f"070076{next(_phone_seq):04d}"


def _wamid() -> str:
    return f"wamid.TEST{next(_wa_seq):06d}"


@dataclass(slots=True)
class ClinicCtx:
    clinic_id: str
    headers: dict[str, str]
    phone_number_id: str


async def _make_clinic(
    admin_client: AsyncClient, client: AsyncClient, *, connect: bool = True
) -> ClinicCtx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Чат",
            "adminPhone": phone,
            "name": "Клиника Чат",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    clinic_id = str(created.json()["id"])
    headers = await otp_login(client, phone)

    phone_number_id = f"7000{next(_pnid_seq):06d}"
    if connect:
        bound = await admin_client.put(
            f"{API}/admin/clients/{clinic_id}/whatsapp",
            json={"phoneNumberId": phone_number_id, "displayPhone": "+996700999000"},
        )
        assert bound.status_code == 200, bound.text
    return ClinicCtx(clinic_id=clinic_id, headers=headers, phone_number_id=phone_number_id)


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    return await _make_clinic(admin_client, client)


def _inbound_payload(
    phone_number_id: str,
    *,
    wa_message_id: str,
    from_phone: str,
    text: str,
    ts: int | None = None,
) -> dict[str, Any]:
    """Payload вебхука Meta с одним текстовым сообщением."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "996700999000",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [{"profile": {"name": "Пациент"}, "wa_id": from_phone}],
                            "messages": [
                                {
                                    "from": from_phone,
                                    "id": wa_message_id,
                                    "timestamp": str(ts or int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


async def _post_webhook(client: AsyncClient, payload: dict[str, Any]) -> None:
    resp = await client.post(f"{API}/whatsapp/webhook", json=payload)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"received": True}


async def _conversations(client: AsyncClient, ctx: ClinicCtx) -> Any:
    resp = await client.get(f"{API}/clinic/conversations", headers=ctx.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1. Вебхук: верификация и подпись ────────────────────────────────────────


async def test_webhook_verify_challenge(client: AsyncClient, monkeypatch: Any) -> None:
    """GET с правильным verify_token возвращает эхо challenge."""
    monkeypatch.setattr(settings, "wa_cloud_verify_token", "vt-secret")
    resp = await client.get(
        f"{API}/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "vt-secret",
            "hub.challenge": "42-challenge",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.text == "42-challenge"

    wrong = await client.get(
        f"{API}/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "x",
        },
    )
    assert wrong.status_code == 403


async def test_webhook_signature_required_when_secret_set(
    client: AsyncClient, ctx: ClinicCtx, monkeypatch: Any
) -> None:
    """Задан APP_SECRET: POST без подписи — 403, с корректной — 200 и сообщение."""
    monkeypatch.setattr(settings, "wa_cloud_app_secret", "top-secret")
    payload = _inbound_payload(
        ctx.phone_number_id,
        wa_message_id=_wamid(),
        from_phone="996700111222",
        text="салам",
    )
    raw = json.dumps(payload).encode()

    unsigned = await client.post(
        f"{API}/whatsapp/webhook", content=raw, headers={"Content-Type": "application/json"}
    )
    assert unsigned.status_code == 403, unsigned.text

    signature = hmac.new(b"top-secret", raw, hashlib.sha256).hexdigest()
    signed = await client.post(
        f"{API}/whatsapp/webhook",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": f"sha256={signature}",
        },
    )
    assert signed.status_code == 200, signed.text
    body = await _conversations(client, ctx)
    assert body["totalCount"] == 1


# ── 2. Входящие ─────────────────────────────────────────────────────────────


async def test_inbound_message_saved_and_visible(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Сообщение пациента появляется в диалогах клиники с открытым окном."""
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id,
            wa_message_id=_wamid(),
            from_phone="996700111333",
            text="Тиш ооруп жатат",
        ),
    )
    body = await _conversations(client, ctx)
    assert body["totalCount"] == 1
    conv = body["list"][0]
    assert conv["waPhone"] == "+996700111333"
    assert conv["status"] == "active"
    assert conv["windowOpen"] is True
    assert conv["lastMessagePreview"] == "Тиш ооруп жатат"
    assert conv["patientId"] is None  # номер пока не заведён в пациентах

    messages = await client.get(
        f"{API}/clinic/conversations/{conv['id']}/messages", headers=ctx.headers
    )
    assert messages.status_code == 200, messages.text
    items = messages.json()["items"]
    assert len(items) == 1
    assert items[0]["role"] == "patient"
    assert items[0]["body"] == "Тиш ооруп жатат"
    assert items[0]["hasMedia"] is False


async def test_duplicate_delivery_creates_no_second_message(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Meta повторила доставку — сообщение в истории одно (дедуп по wamid)."""
    payload = _inbound_payload(
        ctx.phone_number_id,
        wa_message_id=_wamid(),
        from_phone="996700111444",
        text="повтор",
    )
    await _post_webhook(client, payload)
    await _post_webhook(client, payload)
    body = await _conversations(client, ctx)
    conv_id = body["list"][0]["id"]
    messages = await client.get(
        f"{API}/clinic/conversations/{conv_id}/messages", headers=ctx.headers
    )
    assert len(messages.json()["items"]) == 1


async def test_unknown_phone_number_id_ignored(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Событие незнакомого номера: Meta получает 200, диалог не заводится."""
    await _post_webhook(
        client,
        _inbound_payload(
            "999999999", wa_message_id=_wamid(), from_phone="996700111555", text="кто тут"
        ),
    )
    body = await _conversations(client, ctx)
    assert body["totalCount"] == 0


async def test_patient_matched_by_phone(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Номер уже есть в пациентах — диалог сразу привязан к карточке."""
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.headers,
        json={"phone": "+996700111666", "firstName": "Айгүл", "lastName": "Асанова"},
    )
    assert patient.status_code == 201, patient.text
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id,
            wa_message_id=_wamid(),
            from_phone="996700111666",
            text="Саламатсызбы",
        ),
    )
    conv = (await _conversations(client, ctx))["list"][0]
    assert conv["patientId"] == patient.json()["id"]
    assert "Айгүл" in conv["patientName"]


async def test_coexistence_echo_saved_as_admin(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Исходящее человека из WhatsApp Business (эхо синка) ложится ролью admin."""
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id,
            wa_message_id=_wamid(),
            from_phone="996700111777",
            text="здравствуйте",
        ),
    )
    echo = {
        "entry": [
            {
                "changes": [
                    {
                        "field": "smb_message_echoes",
                        "value": {
                            "metadata": {"phone_number_id": ctx.phone_number_id},
                            "message_echoes": [
                                {
                                    "id": _wamid(),
                                    "to": "996700111777",
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": "Ответил из приложения"},
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }
    await _post_webhook(client, echo)
    conv = (await _conversations(client, ctx))["list"][0]
    messages = await client.get(
        f"{API}/clinic/conversations/{conv['id']}/messages", headers=ctx.headers
    )
    items = messages.json()["items"]
    assert [m["role"] for m in items] == ["admin", "patient"]  # новые сверху


# ── 3. Ответ клиники и окно 24 часов ────────────────────────────────────────


async def test_reply_within_window(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Окно открыто — свободный текст уходит (лог-фолбэк) и попадает в историю."""
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id,
            wa_message_id=_wamid(),
            from_phone="996700111888",
            text="можно записаться?",
        ),
    )
    conv_id = (await _conversations(client, ctx))["list"][0]["id"]
    sent = await client.post(
        f"{API}/clinic/conversations/{conv_id}/messages",
        headers=ctx.headers,
        json={"body": "Конечно! Завтра в 11:00 удобно?"},
    )
    assert sent.status_code == 201, sent.text
    msg = sent.json()
    assert msg["role"] == "admin"
    assert msg["sentAt"] is not None
    assert msg["waMessageId"].startswith("log-")  # канал без кредов пишет в лог

    items = (
        await client.get(f"{API}/clinic/conversations/{conv_id}/messages", headers=ctx.headers)
    ).json()["items"]
    assert [m["role"] for m in items] == ["admin", "patient"]


async def test_window_closed_rejects_free_text(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Сутки с сообщения пациента прошли — 422 window_closed, только шаблоны."""
    stale_ts = int(time.time()) - 25 * 3600
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id,
            wa_message_id=_wamid(),
            from_phone="996700111999",
            text="давнее сообщение",
            ts=stale_ts,
        ),
    )
    conv = (await _conversations(client, ctx))["list"][0]
    assert conv["windowOpen"] is False
    resp = await client.post(
        f"{API}/clinic/conversations/{conv['id']}/messages",
        headers=ctx.headers,
        json={"body": "простите, что поздно"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "window_closed"


# ── 4. Доступ ───────────────────────────────────────────────────────────────


async def test_clinics_are_isolated(
    admin_client: AsyncClient, client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Диалог клиники А не виден клинике Б: ни в списке, ни по прямому id."""
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id,
            wa_message_id=_wamid(),
            from_phone="996700112111",
            text="приватное",
        ),
    )
    conv_id = (await _conversations(client, ctx))["list"][0]["id"]

    other = await _make_clinic(admin_client, client)
    listed = await _conversations(client, other)
    assert listed["totalCount"] == 0
    stranger = await client.get(
        f"{API}/clinic/conversations/{conv_id}/messages", headers=other.headers
    )
    assert stranger.status_code == 404


async def test_worker_role_has_no_access(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Врач (роль «Работник») переписку не видит — Р22: отвечает ресепшен."""
    doctor_phone = None
    # own_membership_id заводит врача и возвращает членство; телефон нужен для
    # входа — вытащим его из списка сотрудников.
    await own_membership_id(client, ctx.headers)
    staff = await client.get(f"{API}/clinic/staff", headers=ctx.headers)
    doctor = next(m for m in staff.json() if m["specializationId"])
    doctor_phone = doctor["phone"]
    doctor_headers = await otp_login(client, doctor_phone)
    resp = await client.get(f"{API}/clinic/conversations", headers=doctor_headers)
    assert resp.status_code == 403, resp.text


# ── 5. Привязка номера (админка Skrezo) ─────────────────────────────────────


async def test_binding_conflict_and_deactivation(
    admin_client: AsyncClient, client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Один номер — одна клиника; выключенная привязка перестаёт принимать вебхук."""
    other = await _make_clinic(admin_client, client, connect=False)
    conflict = await admin_client.put(
        f"{API}/admin/clients/{other.clinic_id}/whatsapp",
        json={"phoneNumberId": ctx.phone_number_id, "displayPhone": "+996700999111"},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"] == "phone_number_taken"

    off = await admin_client.put(
        f"{API}/admin/clients/{ctx.clinic_id}/whatsapp",
        json={
            "phoneNumberId": ctx.phone_number_id,
            "displayPhone": "+996700999000",
            "active": False,
        },
    )
    assert off.status_code == 200, off.text
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id,
            wa_message_id=_wamid(),
            from_phone="996700112333",
            text="в закрытую дверь",
        ),
    )
    assert (await _conversations(client, ctx))["totalCount"] == 0


# ── 6. Шаблоны ──────────────────────────────────────────────────────────────

from collections.abc import AsyncIterator  # noqa: E402

import pytest_asyncio  # noqa: E402

from app.main import app  # noqa: E402
from app.modules.whatsapp.presentation.deps import get_whatsapp_client  # noqa: E402
from app.shared.whatsapp_cloud import (  # noqa: E402
    DownloadedMedia,
    SentMessage,
    TemplateInfo,
)


class _FakeGraphClient:
    """Фейковый Cloud API: отдаёт заданные шаблоны, копит отправленное."""

    def __init__(self, templates: list[TemplateInfo]) -> None:
        self.templates = templates
        self.sent_templates: list[tuple[str, str, list[str]]] = []

    async def send_text(self, phone_number_id: str, to_phone: str, body: str) -> SentMessage:
        return SentMessage(wa_message_id=f"fake-text-{len(self.sent_templates)}")

    async def send_template(
        self,
        phone_number_id: str,
        to_phone: str,
        template_name: str,
        lang_code: str,
        variables: list[str],
    ) -> SentMessage:
        self.sent_templates.append((template_name, to_phone, variables))
        return SentMessage(wa_message_id=f"fake-tpl-{len(self.sent_templates)}")

    async def fetch_media(self, media_id: str) -> DownloadedMedia | None:
        return None

    async def fetch_templates(self, waba_id: str) -> list[TemplateInfo] | None:
        return self.templates


_REMINDER = TemplateInfo(
    meta_name="visit_reminder",
    lang="ru",
    category="UTILITY",
    body_preview="Здравствуйте, {{1}}! Напоминаем о приёме {{2}}.",
    variables=["1", "2"],
    status="approved",
)
_PENDING = TemplateInfo(
    meta_name="promo",
    lang="ru",
    category="MARKETING",
    body_preview="Акция месяца!",
    variables=[],
    status="pending",
)


@pytest_asyncio.fixture
async def fake_graph() -> AsyncIterator[_FakeGraphClient]:
    """Подменить клиента Cloud API фейком с двумя шаблонами."""
    fake = _FakeGraphClient([_REMINDER, _PENDING])
    app.dependency_overrides[get_whatsapp_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_whatsapp_client, None)


async def _bind_with_waba(admin_client: AsyncClient, ctx: ClinicCtx) -> None:
    bound = await admin_client.put(
        f"{API}/admin/clients/{ctx.clinic_id}/whatsapp",
        json={
            "phoneNumberId": ctx.phone_number_id,
            "displayPhone": "+996700999000",
            "wabaId": "waba-100",
        },
    )
    assert bound.status_code == 200, bound.text


async def test_templates_sync_full_reconciliation(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: ClinicCtx,
    fake_graph: _FakeGraphClient,
) -> None:
    """Синк приводит каталог к кабинету Meta: новые появляются, удалённые пропадают."""
    await _bind_with_waba(admin_client, ctx)
    synced = await client.post(f"{API}/clinic/whatsapp/templates/sync", headers=ctx.headers)
    assert synced.status_code == 200, synced.text
    names = {t["metaName"]: t for t in synced.json()["list"]}
    assert set(names) == {"visit_reminder", "promo"}
    assert names["visit_reminder"]["status"] == "approved"
    assert names["visit_reminder"]["variables"] == ["1", "2"]

    # Шаблон удалили в кабинете — после повторного синка он пропадает и у нас.
    fake_graph.templates = [_REMINDER]
    again = await client.post(f"{API}/clinic/whatsapp/templates/sync", headers=ctx.headers)
    assert {t["metaName"] for t in again.json()["list"]} == {"visit_reminder"}

    listed = await client.get(f"{API}/clinic/whatsapp/templates", headers=ctx.headers)
    assert listed.status_code == 200
    assert {t["metaName"] for t in listed.json()["list"]} == {"visit_reminder"}


async def test_template_catalog_explains_unavailability_and_labels(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: ClinicCtx,
    fake_graph: _FakeGraphClient,
) -> None:
    """Каталог отдаёт название, подписи переменных и причину недоступности с датой."""
    await _bind_with_waba(admin_client, ctx)
    synced = await client.post(f"{API}/clinic/whatsapp/templates/sync", headers=ctx.headers)
    assert synced.status_code == 200, synced.text
    by_name = {t["metaName"]: t for t in synced.json()["list"]}
    approved = by_name["visit_reminder"]
    assert approved["unavailableReason"] is None
    assert approved["title"] == "visit_reminder"  # у шаблона клиники — имя из Meta
    assert approved["variableLabels"] == ["{{1}}", "{{2}}"]
    assert approved["statusChangedAt"] is not None
    pending = by_name["promo"]
    assert pending["unavailableReason"].startswith("На проверке в Meta с ")

    english = await client.get(
        f"{API}/clinic/whatsapp/templates", headers=ctx.headers, params={"lang": "en"}
    )
    assert english.status_code == 200, english.text
    promo_en = next(t for t in english.json()["list"] if t["metaName"] == "promo")
    assert promo_en["unavailableReason"].startswith("Under review by Meta since ")

    # Meta одобрила шаблон — после сверки причина пропадает.
    fake_graph.templates = [_REMINDER, replace(_PENDING, status="approved")]
    again = await client.post(f"{API}/clinic/whatsapp/templates/sync", headers=ctx.headers)
    promo = next(t for t in again.json()["list"] if t["metaName"] == "promo")
    assert promo["status"] == "approved" and promo["unavailableReason"] is None


async def test_admin_sets_title_and_labels_of_common_template(
    admin_client: AsyncClient, client: AsyncClient, ctx: ClinicCtx, _resources: Resources
) -> None:
    """Skrezo подписывает общий шаблон, кабинет клиники видит подписи; число подписей строгое."""
    # Общий шаблон платформы заводит Skrezo в БД (clinic_id NULL) — API создания нет.
    async with _resources.factory() as session:
        row = WhatsAppTemplate(
            clinic_id=None,
            meta_name="debt_reminder_common",
            lang="ru",
            category="UTILITY",
            body_preview="{{1}}, у вас долг {{2}}.",
            variables=["1", "2"],
            status="approved",
        )
        session.add(row)
        await session.commit()
        template_id = str(row.id)

    listed = await admin_client.get(f"{API}/admin/whatsapp/templates")
    assert listed.status_code == 200, listed.text
    assert template_id in {t["id"] for t in listed.json()["list"]}

    mismatch = await admin_client.patch(
        f"{API}/admin/whatsapp/templates/{template_id}", json={"variableLabels": ["Имя"]}
    )
    assert mismatch.status_code == 422, mismatch.text
    assert mismatch.json()["error"] == "labels_count_mismatch"

    updated = await admin_client.patch(
        f"{API}/admin/whatsapp/templates/{template_id}",
        json={"title": "Напоминание о долге", "variableLabels": ["Имя", "Сумма"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["title"] == "Напоминание о долге"
    assert updated.json()["variableLabels"] == ["Имя", "Сумма"]

    catalog = await client.get(f"{API}/clinic/whatsapp/templates", headers=ctx.headers)
    assert catalog.status_code == 200, catalog.text
    shared = next(t for t in catalog.json()["list"] if t["id"] == template_id)
    assert shared["shared"] is True
    assert shared["title"] == "Напоминание о долге"
    assert shared["variableLabels"] == ["Имя", "Сумма"]

    # Пустое название возвращает имя из Meta; неизвестный id → 404.
    cleared = await admin_client.patch(
        f"{API}/admin/whatsapp/templates/{template_id}", json={"title": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["title"] == "debt_reminder_common"
    assert cleared.json()["variableLabels"] == ["Имя", "Сумма"]  # подписи не тронуты
    missing = await admin_client.patch(
        f"{API}/admin/whatsapp/templates/{uuid.uuid4()}", json={"title": "x"}
    )
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"] == "template_not_found"


async def test_sync_without_creds_keeps_catalog(
    admin_client: AsyncClient, client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Лог-фолбэк (нет кредов Meta) — синк отвечает 502 и каталог не трогает."""
    await _bind_with_waba(admin_client, ctx)
    resp = await client.post(f"{API}/clinic/whatsapp/templates/sync", headers=ctx.headers)
    assert resp.status_code == 502, resp.text
    assert resp.json()["error"] == "wa_templates_unavailable"


async def test_sync_requires_waba(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Привязка без WABA — 422 waba_id_missing: шаблоны спросить не у кого."""
    resp = await client.post(f"{API}/clinic/whatsapp/templates/sync", headers=ctx.headers)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "waba_id_missing"


async def test_send_template_into_closed_window(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: ClinicCtx,
    fake_graph: _FakeGraphClient,
) -> None:
    """Окно закрыто: текст — 422, одобренный шаблон уходит и виден в истории."""
    await _bind_with_waba(admin_client, ctx)
    await client.post(f"{API}/clinic/whatsapp/templates/sync", headers=ctx.headers)
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id,
            wa_message_id=_wamid(),
            from_phone="996700113111",
            text="старое",
            ts=int(time.time()) - 30 * 3600,
        ),
    )
    conv = (await _conversations(client, ctx))["list"][0]
    assert conv["windowOpen"] is False

    templates = (await client.get(f"{API}/clinic/whatsapp/templates", headers=ctx.headers)).json()[
        "list"
    ]
    approved = next(t for t in templates if t["status"] == "approved")
    pending = next(t for t in templates if t["status"] == "pending")

    sent = await client.post(
        f"{API}/clinic/conversations/{conv['id']}/templates",
        headers=ctx.headers,
        json={"templateId": approved["id"], "variables": ["Айгүл", "завтра в 11:00"]},
    )
    assert sent.status_code == 201, sent.text
    msg = sent.json()
    assert msg["role"] == "admin"
    assert msg["body"] == "Здравствуйте, Айгүл! Напоминаем о приёме завтра в 11:00."
    assert fake_graph.sent_templates == [
        ("visit_reminder", "+996700113111", ["Айгүл", "завтра в 11:00"])
    ]

    # Неодобренный шаблон и несовпадение переменных — 422 со своими кодами.
    rejected = await client.post(
        f"{API}/clinic/conversations/{conv['id']}/templates",
        headers=ctx.headers,
        json={"templateId": pending["id"], "variables": []},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"] == "template_not_approved"

    mismatch = await client.post(
        f"{API}/clinic/conversations/{conv['id']}/templates",
        headers=ctx.headers,
        json={"templateId": approved["id"], "variables": ["только одна"]},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"] == "template_variables_mismatch"


_HELLO_WORLD = TemplateInfo(
    meta_name="hello_world",
    lang="en_US",
    category="UTILITY",
    body_preview=(
        "Welcome and congratulations!! This message demonstrates your ability to send "
        "a WhatsApp message notification from the Cloud API, hosted by Meta."
    ),
    variables=[],
    status="approved",
)


async def test_service_templates_hidden_and_rejected(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: ClinicCtx,
    fake_graph: _FakeGraphClient,
    _resources: Resources,
) -> None:
    """Тестовый hello_world Meta: синк не сохраняет, каталог не показывает, отправка — 422."""
    await _bind_with_waba(admin_client, ctx)
    fake_graph.templates = [_REMINDER, _HELLO_WORLD]
    synced = await client.post(f"{API}/clinic/whatsapp/templates/sync", headers=ctx.headers)
    assert synced.status_code == 200, synced.text
    assert {t["metaName"] for t in synced.json()["list"]} == {"visit_reminder"}

    # Строка, оставшаяся со старых сверок: каталог её скрывает, отправить нельзя.
    async with _resources.factory() as session:
        session.add(
            WhatsAppTemplate(
                clinic_id=uuid.UUID(ctx.clinic_id),
                meta_name="hello_world",
                lang="en_US",
                category="UTILITY",
                body_preview=_HELLO_WORLD.body_preview,
                variables=[],
                status="approved",
            )
        )
        await session.commit()
        hidden_id = (
            await session.execute(
                select(WhatsAppTemplate.id).where(WhatsAppTemplate.meta_name == "hello_world")
            )
        ).scalar_one()
    listed = await client.get(f"{API}/clinic/whatsapp/templates", headers=ctx.headers)
    assert {t["metaName"] for t in listed.json()["list"]} == {"visit_reminder"}

    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id,
            wa_message_id=_wamid(),
            from_phone="996700113222",
            text="старое",
            ts=int(time.time()) - 30 * 3600,
        ),
    )
    conv = (await _conversations(client, ctx))["list"][0]
    rejected = await client.post(
        f"{API}/clinic/conversations/{conv['id']}/templates",
        headers=ctx.headers,
        json={"templateId": str(hidden_id), "variables": []},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"] == "template_service"
    assert fake_graph.sent_templates == []


# ── 7. Постоянный диалог: закрытый возобновляется, а не дублируется ──────────


async def _close_conversation(res: Resources, conversation_id: str) -> None:
    """«Сутки тишины прошли»: диалог закрыт cron'ом, отметка учёта пакета стоит."""
    async with res.factory() as session:
        conv = await session.get(AiConversation, uuid.UUID(conversation_id))
        assert conv is not None
        conv.status = AiConversationStatus.CLOSED
        conv.assistant_counted_at = datetime.now(UTC)
        await session.commit()


def _echo_payload(phone_number_id: str, *, to_phone: str, text: str) -> dict[str, Any]:
    """Payload Coexistence: исходящее человека из приложения WhatsApp Business."""
    return {
        "entry": [
            {
                "changes": [
                    {
                        "field": "smb_message_echoes",
                        "value": {
                            "metadata": {"phone_number_id": phone_number_id},
                            "message_echoes": [
                                {
                                    "id": _wamid(),
                                    "to": to_phone,
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ]
            }
        ]
    }


async def test_closed_conversation_is_continued_by_next_message(
    client: AsyncClient, ctx: ClinicCtx, _resources: Resources
) -> None:
    """Пациент написал после закрытия — тот же диалог снова active, а не новая строка."""
    phone = "996700114001"
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id, wa_message_id=_wamid(), from_phone=phone, text="первое"
        ),
    )
    conv = (await _conversations(client, ctx))["list"][0]
    await _close_conversation(_resources, conv["id"])

    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id, wa_message_id=_wamid(), from_phone=phone, text="снова я"
        ),
    )
    listed = (await _conversations(client, ctx))["list"]
    assert [c["id"] for c in listed] == [conv["id"]]
    async with _resources.factory() as session:
        rows = list(
            (
                await session.execute(
                    select(AiConversation).where(AiConversation.wa_phone == f"+{phone}")
                )
            ).scalars()
        )
        assert len(rows) == 1
        assert rows[0].status is AiConversationStatus.ACTIVE
        # Обращение после суток тишины — новый диалог для пакета тарифа.
        assert rows[0].assistant_counted_at is None
    history = await client.get(
        f"{API}/clinic/conversations/{conv['id']}/messages", headers=ctx.headers
    )
    assert [m["body"] for m in history.json()["items"]] == ["снова я", "первое"]


async def test_closed_conversation_is_continued_by_echo(
    client: AsyncClient, ctx: ClinicCtx, _resources: Resources
) -> None:
    """Ответ клиники из приложения WhatsApp Business после закрытия — тот же диалог."""
    phone = "996700114002"
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id, wa_message_id=_wamid(), from_phone=phone, text="вопрос"
        ),
    )
    conv = (await _conversations(client, ctx))["list"][0]
    await _close_conversation(_resources, conv["id"])

    await _post_webhook(
        client, _echo_payload(ctx.phone_number_id, to_phone=phone, text="Ответил из приложения")
    )
    listed = (await _conversations(client, ctx))["list"]
    assert [c["id"] for c in listed] == [conv["id"]]
    async with _resources.factory() as session:
        row = await session.get(AiConversation, uuid.UUID(conv["id"]))
        assert row is not None and row.status is AiConversationStatus.ACTIVE
    history = await client.get(
        f"{API}/clinic/conversations/{conv['id']}/messages", headers=ctx.headers
    )
    assert [m["role"] for m in history.json()["items"]] == ["admin", "patient"]
