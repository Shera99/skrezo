"""Защита от ``null`` в NOT NULL-поля (PATCH) — модули services/patients/clients/appointments.

Класс дефекта общий: поле схемы типизировано как ``X | None`` (чтобы его можно
было «не передавать»), но колонка в БД объявлена NOT NULL. Явный ``null`` в теле
проезжал валидацию, доезжал до слепого ``setattr`` и ронял запрос на 500. Эталон
и подробное описание — ``tests/api/test_schedule_api.py`` (модуль приёмов).

Здесь на каждом модуле проверяется не только код ответа (422 вместо 500), но и
целостность строки: полный JSON карточки до и после отклонённого запроса обязан
совпасть побайтово.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API


async def _provision_clinic(admin_client: AsyncClient, phone: str, name: str) -> str:
    """Завести клинику через админ-провижининг, вернуть временный пароль владельца."""
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
async def clinic(client: AsyncClient, admin_client: AsyncClient) -> dict[str, str]:
    """Заголовки владельца свежей клиники (контур клиники)."""
    temp = await _provision_clinic(admin_client, "0700700001", "Клиника Null-Guard")
    return await _login(client, "0700700001", temp)


# ── services: PATCH /clinic/services/{id} ───────────────────────────────────


async def _create_service(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    resp = await client.post(
        f"{API}/clinic/services",
        headers=headers,
        json={"name": "Пломба", "priceTiyin": 3000, "duration": 30},
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def _service_row(client: AsyncClient, headers: dict[str, str], sid: str) -> dict[str, Any]:
    """Строка услуги из списка (отдельного GET по id у услуг нет)."""
    listing = await client.get(f"{API}/clinic/services", headers=headers)
    assert listing.status_code == 200, listing.text
    return next(s for s in listing.json() if s["id"] == sid)


@pytest.mark.parametrize("field", ["name", "priceTiyin", "active"])
async def test_service_patch_null_into_not_null_is_rejected_and_row_intact(
    client: AsyncClient, clinic: dict[str, str], field: str
) -> None:
    """null в NOT NULL-поле услуги → 422, строка в БД не изменилась."""
    sid = (await _create_service(client, clinic))["id"]
    before = await _service_row(client, clinic, sid)

    resp = await client.patch(
        f"{API}/clinic/services/{sid}", headers=clinic, json={field: None}
    )
    assert resp.status_code == 422, f"{field}: {resp.text}"

    after = await _service_row(client, clinic, sid)
    assert after == before, f"{field}: строка изменилась после отклонённого запроса"


async def test_service_nullable_fields_still_accept_null(
    client: AsyncClient, clinic: dict[str, str]
) -> None:
    """Запрет null не задел NULLABLE-колонки услуги: описание и длительность снимаются."""
    sid = (await _create_service(client, clinic))["id"]
    resp = await client.patch(
        f"{API}/clinic/services/{sid}",
        headers=clinic,
        json={"description": None, "duration": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["duration"] is None


# ── patients: PATCH /clinic/patients/{id} ───────────────────────────────────


async def _create_patient_with_appointment(
    client: AsyncClient, headers: dict[str, str]
) -> str:
    """Глобальный пациент + приём (даёт клинике право видеть/править карту)."""
    pid = (
        await client.post(
            f"{API}/clinic/patients",
            headers=headers,
            json={"phone": "0700701001", "firstName": "Иван", "lastName": "Петров"},
        )
    ).json()["id"]
    appt = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={"patientId": pid, "items": [{"teeth": [46]}]},
    )
    assert appt.status_code == 201, appt.text
    return str(pid)


async def _patient_card(client: AsyncClient, headers: dict[str, str], pid: str) -> dict[str, Any]:
    resp = await client.get(f"{API}/clinic/patients/{pid}", headers=headers)
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


async def test_patient_patch_null_into_not_null_is_rejected_and_row_intact(
    client: AsyncClient, clinic: dict[str, str]
) -> None:
    """null в NOT NULL-поле пациента (firstName) → 422, карточка не изменилась."""
    pid = await _create_patient_with_appointment(client, clinic)
    before = await _patient_card(client, clinic, pid)

    resp = await client.patch(
        f"{API}/clinic/patients/{pid}", headers=clinic, json={"firstName": None}
    )
    assert resp.status_code == 422, resp.text

    after = await _patient_card(client, clinic, pid)
    assert after == before, "карточка пациента изменилась после отклонённого запроса"


async def test_patient_patch_null_via_snake_case_is_also_rejected(
    client: AsyncClient, clinic: dict[str, str]
) -> None:
    """CamelModel принимает snake_case → {"first_name": null} тоже 422, а не мимо валидатора."""
    pid = await _create_patient_with_appointment(client, clinic)
    resp = await client.patch(
        f"{API}/clinic/patients/{pid}", headers=clinic, json={"first_name": None}
    )
    assert resp.status_code == 422, resp.text


async def test_patient_nullable_fields_still_accept_null(
    client: AsyncClient, clinic: dict[str, str]
) -> None:
    """Запрет null не задел NULLABLE-колонки пациента: отчество/пол/email снимаются."""
    pid = await _create_patient_with_appointment(client, clinic)
    resp = await client.patch(
        f"{API}/clinic/patients/{pid}",
        headers=clinic,
        json={"middleName": None, "gender": None, "email": None, "lastName": None},
    )
    assert resp.status_code == 200, resp.text


# ── clients: PATCH /admin/clients/{id} ──────────────────────────────────────


async def _create_client(admin_client: AsyncClient) -> str:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    resp = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": "0700702001",
            "name": "Клиент Null-Guard",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _client_card(admin_client: AsyncClient, cid: str) -> dict[str, Any]:
    resp = await admin_client.get(f"{API}/admin/clients/{cid}")
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


@pytest.mark.parametrize("field", ["name", "language"])
async def test_client_patch_null_into_not_null_is_rejected_and_row_intact(
    admin_client: AsyncClient, field: str
) -> None:
    """null в NOT NULL-поле клиники (name → clinic.name, language → clinic.lang) → 422."""
    cid = await _create_client(admin_client)
    before = await _client_card(admin_client, cid)

    resp = await admin_client.patch(f"{API}/admin/clients/{cid}", json={field: None})
    assert resp.status_code == 422, f"{field}: {resp.text}"

    after = await _client_card(admin_client, cid)
    assert after == before, f"{field}: карточка клиента изменилась после отклонённого запроса"


async def test_client_nullable_fields_still_accept_null(admin_client: AsyncClient) -> None:
    """Запрет null не задел NULLABLE-колонки клиники: адрес/город/телефон/заметки снимаются."""
    cid = await _create_client(admin_client)
    resp = await admin_client.patch(
        f"{API}/admin/clients/{cid}",
        json={"address": None, "cityId": None, "businessPhone": None, "notes": None},
    )
    assert resp.status_code == 200, resp.text


# ── appointments: PATCH /clinic/appointments/{id} ───────────────────────────
#
# NOT NULL-поля приёма (ORM app/shared/db/models/appointment.py): scheduled_at,
# status, appointment_type, duration_min. NULLABLE: comment, doctor_membership_id,
# service_id. Приёмы косвенно затронуты в test_schedule_api.py; здесь — прицельный
# null-guard уровня соседних модулей (services/patients/clients выше).


async def _add_doctor(client: AsyncClient, headers: dict[str, str]) -> str:
    """Врач клиники (membership) — нужен, чтобы у приёма было что занулять в doctorMembershipId."""
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    resp = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": "Врач Null", "phone": "0700703001", "specializationId": spec},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["membershipId"])


async def _create_appointment(client: AsyncClient, headers: dict[str, str]) -> str:
    """Приём с врачом, услугой на 45 минут и одним зубом — вернуть его id.

    Длительность задаётся явно (45), чтобы контроль ``serviceId: null`` мог
    отличить «сохранили 45» от «схлопнули в дефолтные 30».
    """
    pid = (
        await client.post(
            f"{API}/clinic/patients",
            headers=headers,
            json={"phone": "0700703002", "firstName": "Пётр", "lastName": "Приёмов"},
        )
    ).json()["id"]
    doctor = await _add_doctor(client, headers)
    service = (
        await client.post(
            f"{API}/clinic/services",
            headers=headers,
            json={"name": "Лечение 45", "priceTiyin": 3000, "duration": 45},
        )
    ).json()["id"]
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": pid,
            "doctorMembershipId": doctor,
            "serviceId": service,
            "scheduledAt": "2026-07-20T09:00:00+06:00",
            "durationMin": 45,
            "items": [{"teeth": [46]}],
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _appointment_card(
    client: AsyncClient, headers: dict[str, str], aid: str
) -> dict[str, Any]:
    resp = await client.get(f"{API}/clinic/appointments/{aid}", headers=headers)
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


@pytest.mark.parametrize("field", ["scheduledAt", "status", "appointmentType", "durationMin"])
async def test_appointment_patch_null_into_not_null_is_rejected_and_row_intact(
    client: AsyncClient, clinic: dict[str, str], field: str
) -> None:
    """null в NOT NULL-поле приёма → 422, карточка приёма не изменилась побайтово."""
    aid = await _create_appointment(client, clinic)
    before = await _appointment_card(client, clinic, aid)

    resp = await client.patch(
        f"{API}/clinic/appointments/{aid}", headers=clinic, json={field: None}
    )
    assert resp.status_code == 422, f"{field}: {resp.text}"

    after = await _appointment_card(client, clinic, aid)
    assert after == before, f"{field}: карточка приёма изменилась после отклонённого запроса"


@pytest.mark.parametrize("field", ["scheduled_at", "appointment_type", "duration_min"])
async def test_appointment_patch_null_via_snake_case_is_also_rejected(
    client: AsyncClient, clinic: dict[str, str], field: str
) -> None:
    """CamelModel принимает snake_case → null snake-именем тоже 422, а не мимо валидатора."""
    aid = await _create_appointment(client, clinic)
    resp = await client.patch(
        f"{API}/clinic/appointments/{aid}", headers=clinic, json={field: None}
    )
    assert resp.status_code == 422, f"{field}: {resp.text}"


async def test_appointment_nullable_fields_still_accept_null(
    client: AsyncClient, clinic: dict[str, str]
) -> None:
    """Запрет null не задел NULLABLE-колонки приёма: комментарий/врач/услуга снимаются."""
    aid = await _create_appointment(client, clinic)
    resp = await client.patch(
        f"{API}/clinic/appointments/{aid}",
        headers=clinic,
        json={"comment": None, "doctorMembershipId": None, "serviceId": None},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["doctorName"] is None
    assert body["serviceId"] is None


async def test_appointment_clearing_service_keeps_duration(
    client: AsyncClient, clinic: dict[str, str]
) -> None:
    """Снятие услуги (serviceId: null) не сбрасывает durationMin в дефолтные 30."""
    aid = await _create_appointment(client, clinic)
    resp = await client.patch(
        f"{API}/clinic/appointments/{aid}", headers=clinic, json={"serviceId": None}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["durationMin"] == 45
