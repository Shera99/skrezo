"""Интеграционные тесты предзаполнения шаблонов WhatsApp (решение 19.10.2026).

Meta значения ``{{1}}``, ``{{2}}`` сама не подставляет, а кабинет спрашивал их у
сотрудника перед каждой отправкой. Теперь у переменной есть источник:

* клиника задаёт подписи и источники своему шаблону, Skrezo — общему;
  синхронизация с Meta их не стирает;
* ``…/templates/{id}/prefill`` берёт значения из карточки пациента диалога,
  данных клиники, ближайшей будущей записи и долга; не нашлось — ``null``.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.main import app
from app.modules.whatsapp.presentation.deps import get_whatsapp_client
from app.shared.db.models.whatsapp_template import WhatsAppTemplate
from app.shared.whatsapp_cloud import TemplateInfo
from tests.api.test_whatsapp_api import (
    ClinicCtx,
    _bind_with_waba,
    _FakeGraphClient,
    _inbound_payload,
    _make_clinic,
    _post_webhook,
    _wamid,
)
from tests.conftest import API, Resources, own_membership_id

#: Шаблон со всеми видами переменных — по одной на каждый источник проверки.
_FULL = TemplateInfo(
    meta_name="visit_full",
    lang="ru",
    category="UTILITY",
    body_preview="{{1}}, это {{2}}. Запись {{3}} в {{4}}, врач {{5}}. Долг {{6}} сом.",
    variables=["1", "2", "3", "4", "5", "6"],
    status="approved",
)
_SOURCES = [
    "patient_name",
    "clinic_name",
    "appointment_date",
    "appointment_time",
    "doctor_name",
    "debt_amount",
]


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    return await _make_clinic(admin_client, client)


@pytest_asyncio.fixture
async def fake_graph() -> AsyncIterator[_FakeGraphClient]:
    fake = _FakeGraphClient([_FULL])
    app.dependency_overrides[get_whatsapp_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_whatsapp_client, None)


async def _synced_template(
    admin_client: AsyncClient, client: AsyncClient, ctx: ClinicCtx
) -> dict[str, object]:
    await _bind_with_waba(admin_client, ctx)
    synced = await client.post(f"{API}/clinic/whatsapp/templates/sync", headers=ctx.headers)
    assert synced.status_code == 200, synced.text
    template: dict[str, object] = next(
        t for t in synced.json()["list"] if t["metaName"] == "visit_full"
    )
    return template


async def _conversation_with(client: AsyncClient, ctx: ClinicCtx, from_phone: str) -> str:
    """Пациент пишет в WhatsApp — диалог появляется; вернуть его id."""
    await _post_webhook(
        client,
        _inbound_payload(
            ctx.phone_number_id,
            wa_message_id=_wamid(),
            from_phone=from_phone,
            text="Здравствуйте",
            ts=int(time.time()),
        ),
    )
    listed = await client.get(f"{API}/clinic/conversations", headers=ctx.headers)
    assert listed.status_code == 200, listed.text
    return str(next(c["id"] for c in listed.json()["list"] if c["waPhone"] == f"+{from_phone}"))


# ── Подписи и источники ─────────────────────────────────────────────────────


async def test_clinic_sets_sources_of_own_template_and_sync_keeps_them(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: ClinicCtx,
    fake_graph: _FakeGraphClient,
) -> None:
    """Источники ставит клиника; подписи без своих берутся из источников; сверка их не трёт."""
    template = await _synced_template(admin_client, client, ctx)
    assert template["variableSources"] == ["manual"] * 6
    url = f"{API}/clinic/whatsapp/templates/{template['id']}"

    updated = await client.patch(
        url,
        headers=ctx.headers,
        json={"title": "Напоминание о визите", "variableSources": _SOURCES},
    )

    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["title"] == "Напоминание о визите"
    assert body["variableSources"] == _SOURCES
    assert body["variableLabels"][:2] == ["Имя пациента", "Название клиники"]

    again = await client.post(f"{API}/clinic/whatsapp/templates/sync", headers=ctx.headers)
    kept = next(t for t in again.json()["list"] if t["id"] == template["id"])
    assert kept["variableSources"] == _SOURCES and kept["title"] == "Напоминание о визите"

    mismatch = await client.patch(
        url, headers=ctx.headers, json={"variableSources": ["patient_name"]}
    )
    assert mismatch.status_code == 422, mismatch.text
    assert mismatch.json()["error"] == "sources_count_mismatch"
    unknown = await client.patch(url, headers=ctx.headers, json={"variableSources": ["salary"] * 6})
    assert unknown.status_code == 422, unknown.text


async def test_clinic_cannot_relabel_shared_template_but_skrezo_can(
    admin_client: AsyncClient, client: AsyncClient, ctx: ClinicCtx, _resources: Resources
) -> None:
    """Общий шаблон платформы подписывает Skrezo: у клиники — 403 template_shared."""
    async with _resources.factory() as session:
        row = WhatsAppTemplate(
            clinic_id=None,
            meta_name=f"common_{uuid.uuid4().hex[:8]}",
            lang="ru",
            category="UTILITY",
            body_preview="{{1}}, ждём вас {{2}}.",
            variables=["1", "2"],
            status="approved",
        )
        session.add(row)
        await session.commit()
        template_id = str(row.id)

    refused = await client.patch(
        f"{API}/clinic/whatsapp/templates/{template_id}",
        headers=ctx.headers,
        json={"variableSources": ["patient_name", "appointment_date"]},
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"] == "template_shared"

    by_skrezo = await admin_client.patch(
        f"{API}/admin/whatsapp/templates/{template_id}",
        json={"variableSources": ["patient_name", "appointment_date"]},
    )
    assert by_skrezo.status_code == 200, by_skrezo.text
    assert by_skrezo.json()["variableSources"] == ["patient_name", "appointment_date"]


async def test_variable_sources_catalog_speaks_request_language(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    ru = await client.get(f"{API}/clinic/whatsapp/templates/variable-sources", headers=ctx.headers)
    kg = await client.get(
        f"{API}/clinic/whatsapp/templates/variable-sources",
        headers=ctx.headers,
        params={"lang": "kg"},
    )

    assert ru.status_code == 200, ru.text
    labels = {item["key"]: item["label"] for item in ru.json()["list"]}
    assert labels["manual"] == "Вручную" and labels["patient_name"] == "Имя пациента"
    assert {item["key"]: item["label"] for item in kg.json()["list"]}["patient_name"] == (
        "Пациенттин аты"
    )


# ── Предзаполнение ──────────────────────────────────────────────────────────


async def test_prefill_takes_patient_clinic_visit_and_debt(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: ClinicCtx,
    fake_graph: _FakeGraphClient,
) -> None:
    """Все шесть значений собраны из карточки, клиники, ближайшей записи и долга."""
    template = await _synced_template(admin_client, client, ctx)
    await client.patch(
        f"{API}/clinic/whatsapp/templates/{template['id']}",
        headers=ctx.headers,
        json={"variableSources": _SOURCES},
    )
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.headers,
        json={"phone": "+996700554433", "firstName": "Айбек", "lastName": "Асанов"},
    )
    assert patient.status_code == 201, patient.text
    doctor_id = await own_membership_id(client, ctx.headers)

    async def book(hours: int, price_tiyin: int) -> str:
        created = await client.post(
            f"{API}/clinic/appointments",
            headers=ctx.headers,
            json={
                "patientId": patient.json()["id"],
                "doctorMembershipId": doctor_id,
                "scheduledAt": (datetime.now(UTC) + timedelta(hours=hours)).isoformat(),
                "items": [{"priceTiyin": price_tiyin, "quantity": 1}],
                "force": True,
            },
        )
        assert created.status_code == 201, created.text
        return str(created.json()["id"])

    # Прошлый визит завершён без оплаты — долг 1 500 сом; следующий — через двое суток.
    unpaid = await book(30, 150_000)
    done = await client.post(
        f"{API}/clinic/appointments/{unpaid}/complete", headers=ctx.headers, json={}
    )
    assert done.status_code == 200, done.text
    await book(48, 0)
    conversation_id = await _conversation_with(client, ctx, "996700554433")

    prefill = await client.get(
        f"{API}/clinic/conversations/{conversation_id}/templates/{template['id']}/prefill",
        headers=ctx.headers,
    )

    assert prefill.status_code == 200, prefill.text
    body = prefill.json()
    values = [v["value"] for v in body["variables"]]
    starts = (datetime.now(UTC) + timedelta(hours=48)).astimezone(ZoneInfo("Asia/Bishkek"))
    assert values[0] == "Айбек"
    assert values[1] == "Клиника Чат"
    assert values[2] == starts.strftime("%d.%m.%Y")
    assert values[3] is not None and len(values[3]) == 5  # ЧЧ:ММ по часам клиники
    assert values[4]  # врач ближайшей записи
    assert values[5] == "1 500"
    assert body["complete"] is True
    assert [v["placeholder"] for v in body["variables"]] == [f"{{{{{i}}}}}" for i in range(1, 7)]


async def test_prefill_without_patient_card_leaves_patient_values_empty(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: ClinicCtx,
    fake_graph: _FakeGraphClient,
) -> None:
    """Номера нет в карточках — имя, запись и долг пустые, сотрудник впишет; клиника — есть."""
    template = await _synced_template(admin_client, client, ctx)
    await client.patch(
        f"{API}/clinic/whatsapp/templates/{template['id']}",
        headers=ctx.headers,
        json={"variableSources": _SOURCES},
    )
    conversation_id = await _conversation_with(client, ctx, "996700554499")

    prefill = await client.get(
        f"{API}/clinic/conversations/{conversation_id}/templates/{template['id']}/prefill",
        headers=ctx.headers,
    )

    assert prefill.status_code == 200, prefill.text
    values = [v["value"] for v in prefill.json()["variables"]]
    assert values == [None, "Клиника Чат", None, None, None, None]
    assert prefill.json()["complete"] is False


async def test_prefill_of_manual_template_is_all_empty_and_foreign_ids_404(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: ClinicCtx,
    fake_graph: _FakeGraphClient,
) -> None:
    """Источники не заданы — всё «вручную», как раньше; чужой диалог или шаблон — 404."""
    template = await _synced_template(admin_client, client, ctx)
    conversation_id = await _conversation_with(client, ctx, "996700554477")
    url = f"{API}/clinic/conversations/{conversation_id}/templates/{template['id']}/prefill"

    prefill = await client.get(url, headers=ctx.headers)

    assert prefill.status_code == 200, prefill.text
    assert {v["value"] for v in prefill.json()["variables"]} == {None}
    assert {v["source"] for v in prefill.json()["variables"]} == {"manual"}
    other = await _make_clinic(admin_client, client)
    foreign = await client.get(url, headers=other.headers)
    assert foreign.status_code == 404, foreign.text
