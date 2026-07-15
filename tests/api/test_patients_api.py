"""API-тесты клинического ядра: пациенты, визиты, одонтограмма, история зуба."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import API


async def _provision_clinic(admin_client: AsyncClient, phone: str, name: str) -> str:
    """Создать клинику и вернуть временный пароль владельца."""
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
async def owner_headers(client: AsyncClient, admin_client: AsyncClient) -> dict[str, str]:
    temp = await _provision_clinic(admin_client, "0700300001", "Клиника-Пациенты")
    return await _login(client, "0700300001", temp)


async def test_patient_and_visit_flow(client: AsyncClient, owner_headers: dict[str, str]) -> None:
    # Создать пациента.
    created = await client.post(
        f"{API}/clinic/patients",
        headers=owner_headers,
        json={"fullName": "Иван Пациент", "phone": "0700301001", "gender": "male"},
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    # Список.
    listing = await client.get(f"{API}/clinic/patients", headers=owner_headers)
    assert listing.status_code == 200
    assert listing.json()["totalCount"] == 1
    assert listing.json()["list"][0]["fullName"] == "Иван Пациент"

    # Визит с манипуляцией по зубу 11.
    visit = await client.post(
        f"{API}/clinic/patients/{pid}/visits",
        headers=owner_headers,
        json={
            "complaint": "Боль",
            "records": [
                {"tooth": 11, "diagnosis": "Кариес", "treatment": "Пломба", "status": "filled"}
            ],
        },
    )
    assert visit.status_code == 201, visit.text
    vid = visit.json()["id"]

    # Карточка: одонтограмма + счётчики.
    card = await client.get(f"{API}/clinic/patients/{pid}", headers=owner_headers)
    assert card.status_code == 200
    body = card.json()
    assert body["visitsCount"] == 1
    assert len(body["odontogram"]) == 1
    tooth = body["odontogram"][0]
    assert tooth["tooth"] == 11
    assert tooth["status"] == "filled"
    assert tooth["historyCount"] == 1

    # История зуба 11.
    hist = await client.get(f"{API}/clinic/patients/{pid}/teeth/11", headers=owner_headers)
    assert hist.status_code == 200
    assert hist.json()["tooth"] == 11
    assert len(hist.json()["records"]) == 1
    assert hist.json()["records"][0]["diagnosis"] == "Кариес"

    # Список визитов + детали.
    visits = await client.get(f"{API}/clinic/patients/{pid}/visits", headers=owner_headers)
    assert visits.json()[0]["recordsCount"] == 1
    detail = await client.get(
        f"{API}/clinic/patients/{pid}/visits/{vid}", headers=owner_headers
    )
    assert detail.status_code == 200
    assert detail.json()["records"][0]["tooth"] == 11


async def test_invalid_tooth_422(client: AsyncClient, owner_headers: dict[str, str]) -> None:
    created = await client.post(
        f"{API}/clinic/patients", headers=owner_headers, json={"fullName": "Пациент Зуб"}
    )
    pid = created.json()["id"]
    resp = await client.post(
        f"{API}/clinic/patients/{pid}/visits",
        headers=owner_headers,
        json={"records": [{"tooth": 99, "diagnosis": "x"}]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"] == "invalid_tooth"

    hist = await client.get(f"{API}/clinic/patients/{pid}/teeth/99", headers=owner_headers)
    assert hist.status_code == 422


async def test_patient_not_found_404(client: AsyncClient, owner_headers: dict[str, str]) -> None:
    resp = await client.get(
        f"{API}/clinic/patients/00000000-0000-0000-0000-000000000000", headers=owner_headers
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "patient_not_found"


async def test_tenant_isolation(client: AsyncClient, admin_client: AsyncClient) -> None:
    # Клиника A заводит пациента.
    temp_a = await _provision_clinic(admin_client, "0700310001", "Клиника A")
    head_a = await _login(client, "0700310001", temp_a)
    pid = (
        await client.post(
            f"{API}/clinic/patients", headers=head_a, json={"fullName": "Пациент A"}
        )
    ).json()["id"]

    # Клиника B не видит пациента клиники A.
    temp_b = await _provision_clinic(admin_client, "0700320001", "Клиника B")
    head_b = await _login(client, "0700320001", temp_b)
    resp = await client.get(f"{API}/clinic/patients/{pid}", headers=head_b)
    assert resp.status_code == 404

    listing_b = await client.get(f"{API}/clinic/patients", headers=head_b)
    assert listing_b.json()["totalCount"] == 0


async def test_worker_rbac(
    client: AsyncClient, admin_client: AsyncClient, owner_headers: dict[str, str]
) -> None:
    # Добавляем врача в клинику владельца (роль «работник»: view/view_medical, без edit).
    # clinic_id владельца берём из /auth/me.
    me = await client.get(f"{API}/auth/me", headers=owner_headers)
    clinic_id = me.json()["activeClinic"]["clinicId"]
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]
    doc = await admin_client.post(
        f"{API}/admin/clients/{clinic_id}/doctors",
        json={"fullName": "Врач", "phone": "0700301999", "specializationId": spec},
    )
    doc_temp = doc.json()["tempPassword"]
    doc_head = await _login(client, "0700301999", doc_temp)

    # Врач может смотреть список (patients.view), но не создавать (нет patients.edit).
    assert (await client.get(f"{API}/clinic/patients", headers=doc_head)).status_code == 200
    forbidden = await client.post(
        f"{API}/clinic/patients", headers=doc_head, json={"fullName": "Нельзя"}
    )
    assert forbidden.status_code == 403
