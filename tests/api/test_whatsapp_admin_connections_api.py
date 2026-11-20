"""API-тесты списка подключений WhatsApp всех клиник (GET /admin/whatsapp/connections).

Что проверяется:

* команда Skrezo видит и привязанные вручную номера, и подключённые клиникой
  через окно Meta — с названием клиники и способом подключения;
* состояние номера: ``working`` / ``needs_reconnect`` (с причиной) /
  ``disconnected`` — и фильтры по нему и по способу подключения;
* поиск по названию клиники и по цифрам номера без «+»;
* ``summary`` считается по всем подключениям и не зависит от фильтров;
* токен клиники наружу не попадает, а сотрудник клиники к списку не допущен.

База общая для всех тестов файла (и соседних), поэтому числа ``summary``
проверяются приростом, а строки — поиском по уникальному названию клиники.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from tests.api.test_whatsapp_api import ClinicCtx, _make_clinic
from tests.conftest import API, Resources

_URL = f"{API}/admin/whatsapp/connections"
_seq = itertools.count(1)


async def _clinic(
    admin_client: AsyncClient,
    client: AsyncClient,
    res: Resources,
    *,
    name: str,
    display_phone: str,
    self_service: bool = False,
    needs_reconnect: str | None = None,
    active: bool = True,
) -> ClinicCtx:
    """Клиника с номером; способ подключения и состояние — прямо в базе."""
    ctx = await _make_clinic(admin_client, client)
    async with res.factory() as session:
        clinic = await session.get(Clinic, uuid.UUID(ctx.clinic_id))
        assert clinic is not None
        clinic.name = name
        row = (
            await session.execute(
                select(ClinicWhatsApp).where(ClinicWhatsApp.clinic_id == uuid.UUID(ctx.clinic_id))
            )
        ).scalar_one()
        row.display_phone = display_phone
        row.active = active
        if self_service:
            row.connected_via = "embedded_signup"
            row.access_token_enc = "секрет-не-для-списка"
            row.connected_at = datetime.now(UTC) + timedelta(minutes=next(_seq))
            row.verified_name = name
            row.quality_rating = "GREEN"
        if needs_reconnect is not None:
            row.needs_reconnect_at = datetime.now(UTC)
            row.needs_reconnect_reason = needs_reconnect
        await session.commit()
    return ctx


async def _get(admin_client: AsyncClient, **params: Any) -> Any:
    response = await admin_client.get(_URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()


async def test_lists_manual_and_self_service_with_state_and_filters(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    tag = uuid.uuid4().hex[:8]
    before = (await _get(admin_client))["summary"]

    manual = await _clinic(
        admin_client, client, _resources, name=f"Ручная {tag}", display_phone="+996700111001"
    )
    own = await _clinic(
        admin_client,
        client,
        _resources,
        name=f"Сама {tag}",
        display_phone="+996700111002",
        self_service=True,
    )
    broken = await _clinic(
        admin_client,
        client,
        _resources,
        name=f"Отозвала {tag}",
        display_phone="+996700111003",
        self_service=True,
        needs_reconnect="partner_removed",
    )
    off = await _clinic(
        admin_client,
        client,
        _resources,
        name=f"Отключена {tag}",
        display_phone="+996700111004",
        needs_reconnect="token_invalid",
        active=False,
    )

    body = await _get(admin_client, query=tag)
    assert body["totalCount"] == 4
    assert body["currentPage"] == 1 and body["pageSize"] == 25
    by_clinic = {item["clinicId"]: item for item in body["list"]}

    assert by_clinic[manual.clinic_id]["connectedVia"] == "manual"
    assert by_clinic[manual.clinic_id]["state"] == "working"
    assert by_clinic[manual.clinic_id]["clinicName"] == f"Ручная {tag}"
    assert by_clinic[manual.clinic_id]["connectedAt"] is not None

    assert by_clinic[own.clinic_id]["connectedVia"] == "embedded_signup"
    assert by_clinic[own.clinic_id]["state"] == "working"
    assert by_clinic[own.clinic_id]["qualityRating"] == "GREEN"
    assert by_clinic[own.clinic_id]["needsReconnect"] is None

    reconnect = by_clinic[broken.clinic_id]["needsReconnect"]
    assert by_clinic[broken.clinic_id]["state"] == "needs_reconnect"
    assert reconnect["reason"] == "partner_removed"
    assert reconnect["message"]

    # Отключённый номер не «требует переподключения» — он просто выключен.
    assert by_clinic[off.clinic_id]["state"] == "disconnected"
    assert by_clinic[off.clinic_id]["needsReconnect"] is None

    # Свежие подключения сверху: «Отозвала» подключена позже «Сама».
    ids = [item["clinicId"] for item in body["list"]]
    assert ids.index(broken.clinic_id) < ids.index(own.clinic_id)

    # Секреты не отдаются ни под каким именем.
    assert "секрет" not in str(body)
    assert all("accessToken" not in item for item in body["list"])

    own_only = await _get(admin_client, query=tag, connectedVia="embedded_signup")
    assert {i["clinicId"] for i in own_only["list"]} == {own.clinic_id, broken.clinic_id}

    for state, expected in (
        ("working", {manual.clinic_id, own.clinic_id}),
        ("needs_reconnect", {broken.clinic_id}),
        ("disconnected", {off.clinic_id}),
    ):
        page = await _get(admin_client, query=tag, state=state)
        assert {i["clinicId"] for i in page["list"]} == expected, state
        # Числа вкладок — по всем подключениям, фильтр на них не влияет.
        assert page["summary"] == body["summary"]

    after = body["summary"]
    assert after["total"] - before["total"] == 4
    assert after["working"] - before["working"] == 2
    assert after["needsReconnect"] - before["needsReconnect"] == 1
    assert after["disconnected"] - before["disconnected"] == 1
    assert after["embeddedSignup"] - before["embeddedSignup"] == 2
    assert after["manual"] - before["manual"] == 2


async def test_search_by_phone_digits_and_name(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    tag = uuid.uuid4().hex[:8]
    digits = f"99677{next(_seq):03d}{uuid.uuid4().int % 10000:04d}"
    ctx = await _clinic(
        admin_client, client, _resources, name=f"Поиск {tag}", display_phone=f"+{digits}"
    )

    by_digits = await _get(admin_client, query=digits[3:])
    assert [i["clinicId"] for i in by_digits["list"]] == [ctx.clinic_id]

    by_plus = await _get(admin_client, query=f"+{digits}")
    assert [i["clinicId"] for i in by_plus["list"]] == [ctx.clinic_id]

    by_name = await _get(admin_client, query=f"поиск {tag}")
    assert [i["clinicId"] for i in by_name["list"]] == [ctx.clinic_id]

    # «%» в поиске — обычный символ, а не «всё подряд».
    assert (await _get(admin_client, query=f"{tag}%"))["totalCount"] == 0


async def test_only_platform_admin_and_validation(
    admin_client: AsyncClient, client: AsyncClient, public_client: AsyncClient
) -> None:
    ctx = await _make_clinic(admin_client, client)

    # Владелец клиники — не команда Skrezo.
    forbidden = await public_client.get(_URL, headers=ctx.headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"] == "not_platform_admin"

    assert (await public_client.get(_URL)).status_code == 401
    assert (await admin_client.get(_URL, params={"state": "broken"})).status_code == 422
    assert (await admin_client.get(_URL, params={"connectedVia": "api"})).status_code == 422
    assert (await admin_client.get(_URL, params={"page": 0})).status_code == 422
