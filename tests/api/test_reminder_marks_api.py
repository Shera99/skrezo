"""API-тесты отметок «напомнили»: запись, журнал, удаление, RBAC, изоляция клиник.

Ключевые риски:

* отметка по чужому пациенту или чужому приёму не пишется (IDOR → 404);
* журнал клиники не показывает и не удаляет отметки другой клиники;
* право ``reminders.manage`` есть у администратора, но не у работника;
* список пациентов отдаёт ``lastReminderAt`` — экран не звонит дважды.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, otp_login, own_membership_id, working_slot

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070097{next(_phone_seq):04d}"


@dataclass(slots=True)
class ClinicCtx:
    clinic_id: str
    headers: dict[str, str]
    patient_id: str
    appointment_id: str


async def _provision(admin_client: AsyncClient, client: AsyncClient, name: str) -> ClinicCtx:
    """Клиника с владельцем, врачом, пациентом и одним приёмом (пациент «свой»)."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    resp = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": f"Владелец {name}",
            "adminPhone": phone,
            "name": name,
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert resp.status_code == 201, resp.text
    headers = await otp_login(client, phone)
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Осмотр", "priceTiyin": 3000}
    )
    assert service.status_code == 201, service.text
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пётр", "lastName": "Тестовый"},
    )
    assert patient.status_code == 201, patient.text
    body: dict[str, Any] = {
        "patientId": patient.json()["id"],
        "doctorMembershipId": await own_membership_id(client, headers),
        "scheduledAt": working_slot(),
        "items": [{"serviceId": service.json()["id"], "teeth": [46]}],
    }
    appointment = await client.post(f"{API}/clinic/appointments", headers=headers, json=body)
    assert appointment.status_code == 201, appointment.text
    return ClinicCtx(
        clinic_id=str(resp.json()["id"]),
        headers=headers,
        patient_id=str(patient.json()["id"]),
        appointment_id=str(appointment.json()["id"]),
    )


@pytest.fixture
async def clinic_a(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    return await _provision(admin_client, client, "Напоминания А")


@pytest.fixture
async def clinic_b(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    return await _provision(admin_client, client, "Напоминания Б")


def _mark(ctx: ClinicCtx, kind: str = "recall", **extra: Any) -> dict[str, Any]:
    return {"patientId": ctx.patient_id, "kind": kind, **extra}


async def test_mark_is_visible_in_journal_and_patient_list(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Отметка записывается с именами и видна в журнале и как lastReminderAt пациента."""
    created = await client.post(
        f"{API}/clinic/reminders/marks",
        headers=clinic_a.headers,
        json=_mark(
            clinic_a,
            appointmentId=clinic_a.appointment_id,
            comment="Позвонили, обещал прийти",
        ),
    )
    assert created.status_code == 201, created.text
    mark = created.json()
    assert mark["kind"] == "recall"
    assert mark["patientName"] == "Тестовый Пётр"
    assert mark["markedByName"] == "Владелец Напоминания А"
    assert mark["appointmentId"] == clinic_a.appointment_id
    assert mark["markedAt"].endswith("+06:00")  # таймзона клиники

    journal = await client.get(f"{API}/clinic/reminders/marks", headers=clinic_a.headers)
    assert journal.status_code == 200, journal.text
    assert journal.json()["totalCount"] == 1
    assert journal.json()["list"][0]["id"] == mark["id"]

    # Фильтры: по поводу и по пациенту.
    by_kind = await client.get(
        f"{API}/clinic/reminders/marks", headers=clinic_a.headers, params={"kind": "debt"}
    )
    assert by_kind.json()["totalCount"] == 0
    by_patient = await client.get(
        f"{API}/clinic/reminders/marks",
        headers=clinic_a.headers,
        params={"patientId": clinic_a.patient_id},
    )
    assert by_patient.json()["totalCount"] == 1

    patients = await client.get(f"{API}/clinic/patients", headers=clinic_a.headers)
    assert patients.status_code == 200, patients.text
    row = next(p for p in patients.json()["list"] if p["id"] == clinic_a.patient_id)
    # Тот же момент: список отдаёт его в UTC, отметка — в таймзоне клиники.
    assert datetime.fromisoformat(row["lastReminderAt"]) == datetime.fromisoformat(mark["markedAt"])


async def test_mark_requires_patient_and_appointment_of_this_clinic(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Чужой пациент или чужой приём → 404: журнал одной клиники не ссылается на другую."""
    foreign_patient = await client.post(
        f"{API}/clinic/reminders/marks", headers=clinic_a.headers, json=_mark(clinic_b)
    )
    assert foreign_patient.status_code == 404, foreign_patient.text
    assert foreign_patient.json()["error"] == "patient_not_found"

    foreign_appointment = await client.post(
        f"{API}/clinic/reminders/marks",
        headers=clinic_a.headers,
        json=_mark(clinic_a, appointmentId=clinic_b.appointment_id),
    )
    assert foreign_appointment.status_code == 404, foreign_appointment.text
    assert foreign_appointment.json()["error"] == "appointment_not_found"

    # Ничего не записалось.
    journal = await client.get(f"{API}/clinic/reminders/marks", headers=clinic_a.headers)
    assert journal.json()["totalCount"] == 0


async def test_delete_mark_is_scoped_to_clinic(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Снять отметку может только своя клиника; чужой журнал её не видит."""
    created = await client.post(
        f"{API}/clinic/reminders/marks", headers=clinic_a.headers, json=_mark(clinic_a, "debt")
    )
    assert created.status_code == 201, created.text
    mark_id = created.json()["id"]

    other_journal = await client.get(f"{API}/clinic/reminders/marks", headers=clinic_b.headers)
    assert other_journal.json()["totalCount"] == 0
    foreign = await client.delete(
        f"{API}/clinic/reminders/marks/{mark_id}", headers=clinic_b.headers
    )
    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["error"] == "reminder_mark_not_found"

    deleted = await client.delete(
        f"{API}/clinic/reminders/marks/{mark_id}", headers=clinic_a.headers
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["id"] == mark_id
    journal = await client.get(f"{API}/clinic/reminders/marks", headers=clinic_a.headers)
    assert journal.json()["totalCount"] == 0
    again = await client.delete(f"{API}/clinic/reminders/marks/{mark_id}", headers=clinic_a.headers)
    assert again.status_code == 404


async def test_worker_without_permission_is_forbidden(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Работник (врач) не управляет напоминаниями — права reminders.manage у роли нет."""
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    added = await client.post(
        f"{API}/clinic/staff",
        headers=clinic_a.headers,
        json={"fullName": "Врач Работник", "phone": phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    worker = await otp_login(client, phone)

    denied = await client.post(
        f"{API}/clinic/reminders/marks", headers=worker, json=_mark(clinic_a)
    )
    assert denied.status_code == 403, denied.text
    listing = await client.get(f"{API}/clinic/reminders/marks", headers=worker)
    assert listing.status_code == 403, listing.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/clinic/reminders/marks"),
        ("POST", "/clinic/reminders/marks"),
        ("DELETE", f"/clinic/reminders/marks/{uuid.uuid4()}"),
    ],
)
async def test_reminder_endpoints_without_token_return_401(
    client: AsyncClient, method: str, path: str
) -> None:
    resp = await client.request(method, f"{API}{path}")
    assert resp.status_code == 401, resp.text
