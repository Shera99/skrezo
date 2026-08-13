"""Тесты журнала действий клиники.

Аудит писался только для платформенной зоны, и клиника не могла ответить на
вопрос «кто это сделал»: кто отменил приём, кто дал скидку, кто провёл возврат
из кассы. Скидка ограничена потолком роли, возврат — это деньги, и оба действия
делает человек: без журнала спор упирался в ничью.

Ключевые риски:

* запись должна появляться на самих денежных операциях, а не «когда-нибудь»;
* журнал одной клиники не должен быть виден другой — изоляция идёт по клинике
  **из токена**, параметра клиники в ручке нет вовсе;
* без права `audit.view` журнал закрыт (у врача его нет).
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, otp_login, own_membership_id, working_slot

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070098{next(_phone_seq):04d}"


async def _clinic(admin_client: AsyncClient, client: AsyncClient, name: str) -> dict[str, Any]:
    """Клиника с владельцем, пациентом, услугой и приёмом."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Журнала",
            "adminPhone": phone,
            "name": name,
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(client, phone)

    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Журналов"},
    )
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Лечение", "priceTiyin": 4000}
    )
    appointment = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": patient.json()["id"],
            "doctorMembershipId": await own_membership_id(client, headers),
            "scheduledAt": working_slot(),
            "items": [{"serviceId": service.json()["id"], "teeth": [46]}],
        },
    )
    assert appointment.status_code == 201, appointment.text
    return {
        "id": created.json()["id"],
        "headers": headers,
        "patient": patient.json()["id"],
        "service": service.json()["id"],
        "appointment": appointment.json()["id"],
    }


async def _actions(client: AsyncClient, headers: dict[str, str]) -> list[str]:
    resp = await client.get(f"{API}/clinic/audit", headers=headers, params={"limit": 100})
    assert resp.status_code == 200, resp.text
    return [item["action"] for item in resp.json()["items"]]


# ── 1. Деньги и права попадают в журнал ─────────────────────────────────────


async def test_money_operations_are_recorded(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Скидка, платёж и возврат видны в журнале с автором и суммой."""
    ctx = await _clinic(admin_client, client, "Клиника Денег")
    headers = ctx["headers"]

    discounted = await client.put(
        f"{API}/clinic/appointments/{ctx['appointment']}/discount",
        headers=headers,
        json={"percent": 10},
    )
    assert discounted.status_code == 200, discounted.text

    paid = await client.post(
        f"{API}/clinic/cash/payments",
        headers=headers,
        json={"appointmentId": ctx["appointment"], "amountTiyin": 1000, "method": "cash"},
    )
    assert paid.status_code == 201, paid.text

    refunded = await client.post(
        f"{API}/clinic/cash/payments/{paid.json()['id']}/refund",
        headers=headers,
        json={"amountTiyin": 400},
    )
    assert refunded.status_code == 201, refunded.text

    journal = await client.get(f"{API}/clinic/audit", headers=headers)
    assert journal.status_code == 200, journal.text
    items = journal.json()["items"]
    actions = [i["action"] for i in items]
    assert actions[:3] == ["cash.refund", "cash.payment", "appointment.discount"]

    refund_entry = items[0]
    assert refund_entry["payload"]["amountTiyin"] == 400
    assert refund_entry["actorLogin"] == "Владелец Журнала"

    discount_entry = next(i for i in items if i["action"] == "appointment.discount")
    assert discount_entry["payload"]["to"] == 10
    assert discount_entry["entityId"] == ctx["appointment"]


async def test_clinical_and_staff_changes_are_recorded(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Отмена приёма, правка пациента, прайс и сотрудники — тоже в журнале."""
    ctx = await _clinic(admin_client, client, "Клиника Правок")
    headers = ctx["headers"]
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]

    await client.patch(
        f"{API}/clinic/appointments/{ctx['appointment']}",
        headers=headers,
        json={"status": "cancelled", "cancelReason": "patient_request"},
    )
    await client.patch(
        f"{API}/clinic/patients/{ctx['patient']}", headers=headers, json={"firstName": "Новое"}
    )
    await client.patch(
        f"{API}/clinic/services/{ctx['service']}", headers=headers, json={"priceTiyin": 9000}
    )
    await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": "Врач", "phone": _phone(), "specializationId": spec},
    )

    actions = await _actions(client, headers)
    for expected in ("appointment.update", "patient.update", "service.update", "staff.add"):
        assert expected in actions, f"{expected} не попал в журнал: {actions}"


# ── 2. Изоляция и права ─────────────────────────────────────────────────────


async def test_journal_is_isolated_between_clinics(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Соседняя клиника не видит чужих действий — отбор идёт по токену."""
    first = await _clinic(admin_client, client, "Клиника Первая")
    second = await _clinic(admin_client, client, "Клиника Вторая")

    await client.put(
        f"{API}/clinic/appointments/{first['appointment']}/discount",
        headers=first["headers"],
        json={"percent": 15},
    )

    mine = await _actions(client, first["headers"])
    neighbour_items = (
        await client.get(f"{API}/clinic/audit", headers=second["headers"])
    ).json()["items"]

    assert "appointment.discount" in mine
    assert all(i["action"] != "appointment.discount" for i in neighbour_items)
    # У соседа свой журнал не пуст — он тоже что-то делал, просто не это.
    assert neighbour_items


async def test_journal_needs_permission(admin_client: AsyncClient, client: AsyncClient) -> None:
    """У работника права `audit.view` нет — журнал ему закрыт."""
    ctx = await _clinic(admin_client, client, "Клиника Прав")
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]
    worker_phone = _phone()
    added = await client.post(
        f"{API}/clinic/staff",
        headers=ctx["headers"],
        json={"fullName": "Работник", "phone": worker_phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text

    worker_headers = await otp_login(client, worker_phone)

    resp = await client.get(f"{API}/clinic/audit", headers=worker_headers)
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("path", ["/clinic/audit"])
async def test_journal_needs_auth(client: AsyncClient, path: str) -> None:
    resp = await client.get(f"{API}{path}")
    assert resp.status_code in (401, 403), resp.text


# ── 3. Платформенный журнал не смешивается с клиничным ──────────────────────


async def test_platform_journal_does_not_show_clinic_actions(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """`/admin/audit` остаётся журналом Skrezo, а не лентой всех приёмов.

    Иначе экран поддержки утонул бы в записях о каждом платеже и каждой правке
    пациента — их тысячи против десятков платформенных.
    """
    ctx = await _clinic(admin_client, client, "Клиника Разделения")
    await client.put(
        f"{API}/clinic/appointments/{ctx['appointment']}/discount",
        headers=ctx["headers"],
        json={"percent": 5},
    )

    platform = await admin_client.get(f"{API}/admin/audit", params={"limit": 100})
    assert platform.status_code == 200, platform.text
    actions = [i["action"] for i in platform.json()["items"]]

    assert "clinic.created" in actions  # создание клиники командой Skrezo
    assert "appointment.discount" not in actions
