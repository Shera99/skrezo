"""API-тесты клинического ядра v2: глобальный пациент, приёмы, услуги, история зуба."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import API


async def _provision_clinic(admin_client: AsyncClient, phone: str, name: str) -> str:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    resp = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": phone,
            "name": name,
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tempPassword"])


async def _login(client: AsyncClient, phone: str, password: str) -> dict[str, str]:
    resp = await client.post(f"{API}/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


@pytest.fixture
async def clinic_a(client: AsyncClient, admin_client: AsyncClient) -> dict[str, str]:
    temp = await _provision_clinic(admin_client, "0700400001", "Клиника A")
    return await _login(client, "0700400001", temp)


async def test_service_crud(client: AsyncClient, clinic_a: dict[str, str]) -> None:
    created = await client.post(
        f"{API}/clinic/services",
        headers=clinic_a,
        json={"name": "Пломба", "price": 3000, "duration": 30},
    )
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    assert created.json()["price"] == 3000

    listing = await client.get(f"{API}/clinic/services", headers=clinic_a)
    assert len(listing.json()) == 1

    patched = await client.patch(
        f"{API}/clinic/services/{sid}", headers=clinic_a, json={"active": False}
    )
    assert patched.json()["active"] is False
    active_only = await client.get(
        f"{API}/clinic/services?activeOnly=true", headers=clinic_a
    )
    assert active_only.json() == []


async def test_patient_appointment_and_odontogram(
    client: AsyncClient, clinic_a: dict[str, str]
) -> None:
    # Услуга.
    sid = (
        await client.post(
            f"{API}/clinic/services", headers=clinic_a, json={"name": "Пломба", "price": 3000}
        )
    ).json()["id"]

    # Глобальный пациент.
    created = await client.post(
        f"{API}/clinic/patients",
        headers=clinic_a,
        json={"phone": "0700401001", "firstName": "Иван", "lastName": "Петров"},
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    # Дубликат телефона → 409 с patientId существующего.
    dup = await client.post(
        f"{API}/clinic/patients",
        headers=clinic_a,
        json={"phone": "0700401001", "firstName": "Дубль"},
    )
    assert dup.status_code == 409
    assert dup.json()["patientId"] == pid

    # Поиск по телефону.
    found = await client.get(
        f"{API}/clinic/patients/lookup?phone=0700401001", headers=clinic_a
    )
    assert found.json()["patient"]["id"] == pid

    # До приёма клиника не видит карту (нет связи).
    assert (await client.get(f"{API}/clinic/patients/{pid}", headers=clinic_a)).status_code == 403

    # Приём: цена из услуги (price не передаём) → priceTotal 3000; зуб 46 заводится.
    appt = await client.post(
        f"{API}/clinic/appointments",
        headers=clinic_a,
        json={"patientId": pid, "teeth": [{"tooth": 46, "serviceId": sid}]},
    )
    assert appt.status_code == 201, appt.text
    assert appt.json()["priceTotal"] == 3000
    aid = appt.json()["id"]

    # Теперь пациент в списке клиники, карта доступна, одонтограмма содержит зуб 46.
    listing = await client.get(f"{API}/clinic/patients", headers=clinic_a)
    assert listing.json()["totalCount"] == 1
    card = await client.get(f"{API}/clinic/patients/{pid}", headers=clinic_a)
    assert card.status_code == 200
    assert card.json()["appointmentsCount"] == 1
    assert [t["tooth"] for t in card.json()["odontogram"]] == [46]

    # Upsert одонтограммы (карта зубов).
    up = await client.put(
        f"{API}/clinic/patients/{pid}/teeth",
        headers=clinic_a,
        json={"teeth": [{"tooth": 11, "status": "healthy"}, {"tooth": 46, "status": "filling"}]},
    )
    assert up.status_code == 200
    statuses = {t["tooth"]: t["status"] for t in up.json()["odontogram"]}
    assert statuses[46] == "filling" and statuses[11] == "healthy"

    # История зуба 46.
    hist = await client.get(f"{API}/clinic/patients/{pid}/teeth/46", headers=clinic_a)
    assert hist.status_code == 200
    assert len(hist.json()["records"]) == 1
    assert hist.json()["records"][0]["serviceName"] == "Пломба"
    assert hist.json()["records"][0]["clinicName"] == "Клиника A"

    # Детали приёма + смена статуса.
    detail = await client.get(f"{API}/clinic/appointments/{aid}", headers=clinic_a)
    assert detail.json()["teeth"][0]["tooth"] == 46
    upd = await client.patch(
        f"{API}/clinic/appointments/{aid}", headers=clinic_a, json={"status": "completed"}
    )
    assert upd.json()["status"] == "completed"

    # Некорректный зуб.
    bad = await client.post(
        f"{API}/clinic/appointments",
        headers=clinic_a,
        json={"patientId": pid, "teeth": [{"tooth": 99}]},
    )
    assert bad.status_code == 422
    assert bad.json()["error"] == "invalid_tooth"


async def test_shared_history_across_clinics(
    client: AsyncClient, admin_client: AsyncClient, clinic_a: dict[str, str]
) -> None:
    # Клиника A лечит пациента (зуб 46, услуга A).
    sa = (
        await client.post(
            f"{API}/clinic/services", headers=clinic_a, json={"name": "Пломба A", "price": 3000}
        )
    ).json()["id"]
    pid = (
        await client.post(
            f"{API}/clinic/patients",
            headers=clinic_a,
            json={"phone": "0700402002", "firstName": "Общий"},
        )
    ).json()["id"]
    await client.post(
        f"{API}/clinic/appointments",
        headers=clinic_a,
        json={"patientId": pid, "teeth": [{"tooth": 46, "serviceId": sa}]},
    )

    # Клиника B — тот же глобальный пациент (по телефону), свой приём на зуб 46.
    temp_b = await _provision_clinic(admin_client, "0700402001", "Клиника B")
    clinic_b = await _login(client, "0700402001", temp_b)
    lookup = await client.get(
        f"{API}/clinic/patients/lookup?phone=0700402002", headers=clinic_b
    )
    assert lookup.json()["patient"]["id"] == pid  # тот же глобальный пациент
    sb = (
        await client.post(
            f"{API}/clinic/services", headers=clinic_b, json={"name": "Коронка B", "price": 8000}
        )
    ).json()["id"]
    await client.post(
        f"{API}/clinic/appointments",
        headers=clinic_b,
        json={"patientId": pid, "teeth": [{"tooth": 46, "serviceId": sb}]},
    )

    # Клиника B видит СКВОЗНУЮ историю зуба 46 — обе клиники.
    hist = await client.get(f"{API}/clinic/patients/{pid}/teeth/46", headers=clinic_b)
    assert hist.status_code == 200
    clinics = {r["clinicName"] for r in hist.json()["records"]}
    assert clinics == {"Клиника A", "Клиника B"}
    assert len(hist.json()["records"]) == 2
