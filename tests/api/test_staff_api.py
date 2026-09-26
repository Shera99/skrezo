"""Сотрудники клиники: без специальности, возврат после удаления, снятие специальности.

Этап 1 запросов фронта (К-10, К-11, К-12) и решения владельца от 26.09.2026:

* сотрудник без специальности (администратор) заводится и занимает место —
  лимит считает всех активных, кроме владельца, как подписка;
* исключённого возвращают тем же членством, если есть свободное место;
* ``specializationId: null`` снимает специальность, но не у врача с будущими
  записями — 409 со списком записей; поле не передано — не трогается.
"""

from __future__ import annotations

import itertools
from typing import Any

from httpx import AsyncClient

from tests.conftest import API, otp_login, working_slot

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070098{next(_phone_seq):04d}"


async def _clinic(admin_client: AsyncClient, client: AsyncClient, *, seats: int) -> dict[str, str]:
    """Клиника с ``seats`` местами; заголовки владельца."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    owner = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Штат",
            "adminPhone": owner,
            "name": "Клиника Штат",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": seats,
        },
    )
    assert created.status_code == 201, created.text
    return await otp_login(client, owner)


async def _spec(client: AsyncClient) -> str:
    return str((await client.get(f"{API}/specializations")).json()[0]["id"])


async def _add(
    client: AsyncClient, headers: dict[str, str], *, spec: str | None
) -> tuple[Any, str]:
    """Ответ добавления сотрудника и его телефон."""
    phone = _phone()
    body: dict[str, Any] = {"fullName": "Сотрудник Тестов", "phone": phone}
    if spec is not None:
        body["specializationId"] = spec
    return await client.post(f"{API}/clinic/staff", headers=headers, json=body), phone


async def _members(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    resp = await client.get(f"{API}/clinic/staff", headers=headers)
    assert resp.status_code == 200, resp.text
    return {m["membershipId"]: m for m in resp.json()}


async def test_staff_without_specialization_takes_a_seat(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    headers = await _clinic(admin_client, client, seats=2)
    admin, _ = await _add(client, headers, spec=None)
    assert admin.status_code == 201, admin.text
    admin_id = admin.json()["membershipId"]
    assert (await _members(client, headers))[admin_id]["specializationId"] is None

    doctor, _ = await _add(client, headers, spec=await _spec(client))
    assert doctor.status_code == 201, doctor.text
    # Оба места заняты: администратор без специальности тоже считается.
    third, _ = await _add(client, headers, spec=None)
    assert third.status_code == 409 and third.json()["error"] == "seats_limit_reached"
    subscription = await client.get(f"{API}/clinic/subscription", headers=headers)
    assert subscription.json()["seatsUsed"] == 2  # тот же счёт, что у лимита


async def test_removed_staff_comes_back_while_a_seat_is_free(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    headers = await _clinic(admin_client, client, seats=2)
    spec = await _spec(client)
    added, phone = await _add(client, headers, spec=spec)
    worker_id = added.json()["membershipId"]
    worker = await otp_login(client, phone)
    removed = await client.delete(f"{API}/clinic/staff/{worker_id}", headers=headers)
    assert removed.status_code == 200, removed.text
    denied = await client.get(f"{API}/clinic/services", headers=worker)
    assert denied.status_code == 403 and denied.json()["error"] == "membership_revoked"
    # Завести того же человека заново нельзя — номер занят его аккаунтом.
    again = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": "Снова", "phone": phone, "specializationId": spec},
    )
    assert again.status_code == 409 and again.json()["error"] == "phone_already_used"

    # Пока его не было, места заняли — вернуть некуда.
    first, _ = await _add(client, headers, spec=spec)
    await _add(client, headers, spec=None)
    full = await client.post(f"{API}/clinic/staff/{worker_id}/restore", headers=headers)
    assert full.status_code == 409 and full.json()["error"] == "seats_limit_reached"

    await client.delete(f"{API}/clinic/staff/{first.json()['membershipId']}", headers=headers)
    restored = await client.post(f"{API}/clinic/staff/{worker_id}/restore", headers=headers)
    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["active"] is True and body["specializationId"] == spec
    assert (await client.get(f"{API}/clinic/services", headers=worker)).status_code == 200
    # Повтор ничего не меняет, чужой id — 404.
    repeat = await client.post(f"{API}/clinic/staff/{worker_id}/restore", headers=headers)
    assert repeat.status_code == 200 and repeat.json()["active"] is True
    missing = await client.post(
        f"{API}/clinic/staff/00000000-0000-4000-8000-000000000000/restore", headers=headers
    )
    assert missing.status_code == 404


async def test_specialization_is_kept_while_doctor_has_future_appointments(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    headers = await _clinic(admin_client, client, seats=5)
    spec = await _spec(client)
    added, _ = await _add(client, headers, spec=spec)
    doctor_id = added.json()["membershipId"]
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Айгүл", "lastName": "Асанова"},
    )
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": patient.json()["id"],
            "doctorMembershipId": doctor_id,
            "scheduledAt": working_slot(),
        },
    )
    assert booked.status_code == 201, booked.text
    appointment_id = booked.json()["id"]
    url = f"{API}/clinic/staff/{doctor_id}"

    refused = await client.put(url, headers=headers, json={"specializationId": None})
    assert refused.status_code == 409, refused.text
    body = refused.json()
    assert body["error"] == "doctor_has_future_appointments" and body["count"] == 1
    assert body["appointments"][0]["id"] == appointment_id
    assert body["appointments"][0]["patientName"] == "Асанова Айгүл"

    # Поле не передано — специальность не трогается; null в обязательном поле — 422.
    renamed = await client.put(url, headers=headers, json={"fullName": "Врач Переименован"})
    assert renamed.status_code == 200 and renamed.json()["specializationId"] == spec
    bad = await client.put(url, headers=headers, json={"fullName": None})
    assert bad.status_code == 422

    # Запись отменили — специальность снимается, врач стал администратором.
    cancelled = await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=headers,
        json={"status": "cancelled", "cancelReason": "patient_request"},
    )
    assert cancelled.status_code == 200, cancelled.text
    cleared = await client.put(url, headers=headers, json={"specializationId": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["specializationId"] is None


async def test_owner_can_clear_own_specialization(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    headers = await _clinic(admin_client, client, seats=2)
    owner_id = next(i for i, m in (await _members(client, headers)).items() if m["isOwner"])
    url = f"{API}/clinic/staff/{owner_id}"
    assert (
        await client.put(url, headers=headers, json={"specializationId": await _spec(client)})
    ).status_code == 200
    cleared = await client.put(url, headers=headers, json={"specializationId": None})
    assert cleared.status_code == 200 and cleared.json()["specializationId"] is None
    # Пустая правка владельца по-прежнему закрыта.
    empty = await client.put(url, headers=headers, json={})
    assert empty.status_code == 403 and empty.json()["error"] == "cannot_edit_owner"
