"""API-тесты сброса пароля (админ-сайд для владельца, клиент-сайд для работника)."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import API


async def test_admin_reset_admin_password(client: AsyncClient, admin_client: AsyncClient) -> None:
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Сброс", "adminPhone": "0700910001"},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    temp1 = created.json()["tempPassword"]

    # Старый временный пароль сейчас работает.
    ok1 = await client.post(
        f"{API}/auth/login", json={"phone": "0700910001", "password": temp1}
    )
    assert ok1.status_code == 200

    # Админ сбрасывает → новый временный пароль.
    reset = await admin_client.post(f"{API}/admin/clients/{cid}/reset-password")
    assert reset.status_code == 200, reset.text
    temp2 = reset.json()["tempPassword"]
    assert temp2 != temp1

    # Старый больше не подходит, новый — подходит и требует смены.
    old = await client.post(f"{API}/auth/login", json={"phone": "0700910001", "password": temp1})
    assert old.status_code == 401
    new = await client.post(f"{API}/auth/login", json={"phone": "0700910001", "password": temp2})
    assert new.status_code == 200
    assert new.json()["mustChangePassword"] is True


async def test_owner_resets_worker_password(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]

    clinic = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": "0700920001",
            "name": "Клиника",
            "cityId": city,
            "businessPhone": "0700920000",
            "seats": 5,
        },
    )
    cid = clinic.json()["id"]
    owner_temp = clinic.json()["tempPassword"]

    doctor = await admin_client.post(
        f"{API}/admin/clients/{cid}/doctors",
        json={"fullName": "Врач", "phone": "0700920002", "specializationId": spec},
    )
    doc_id = doctor.json()["id"]

    owner_login = await client.post(
        f"{API}/auth/login", json={"phone": "0700920001", "password": owner_temp}
    )
    owner_token = owner_login.json()["accessToken"]

    reset = await client.post(
        f"{API}/clinic/staff/{doc_id}/reset-password",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert reset.status_code == 200, reset.text
    worker_temp = reset.json()["tempPassword"]

    worker_login = await client.post(
        f"{API}/auth/login", json={"phone": "0700920002", "password": worker_temp}
    )
    assert worker_login.status_code == 200
    assert worker_login.json()["mustChangePassword"] is True
