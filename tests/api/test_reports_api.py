"""API-тесты отчётов: клиентский контур (RBAC own/all) и платформенный (Skrezo).

Ключевые риски:

* RBAC — врач (``reports.view_own``) видит только свои приёмы, владелец
  (``reports.view_all``) — всю клинику;
* изоляция клиник — отчёты клиники A не содержат данных клиники B;
* платформенный контур закрыт от сотрудников клиник (нужен ``is_platform_admin``);
* корректность агрегатов: выручка, долг, средний чек, новые/повторные пациенты.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient

from tests.conftest import API

BISHKEK = ZoneInfo("Asia/Bishkek")
# Часы внутри рабочего окна клиники по умолчанию (09:00–18:00), мимо обеда 12:00–13:00.
_WORK_HOURS = (9, 10, 11, 14, 15, 16, 17)

_phone_seq = itertools.count(1)
_slot_seq = itertools.count()


def _phone() -> str:
    return f"070096{next(_phone_seq):04d}"


def _report_day() -> date:
    """Ближайший рабочий день клиники (воскресенье по умолчанию выходное)."""
    today = datetime.now(BISHKEK).date()
    return today if today.weekday() != 6 else today - timedelta(days=1)


def _slot() -> str:
    """Уникальный слот в рабочем окне — приёмы одного врача не должны пересекаться.

    Приём по умолчанию длится 30 минут, поэтому шаг между слотами — час/полчаса.
    """
    index = next(_slot_seq)
    hour = _WORK_HOURS[index % len(_WORK_HOURS)]
    minute = 30 * ((index // len(_WORK_HOURS)) % 2)
    return datetime.combine(_report_day(), time(hour, minute), tzinfo=BISHKEK).isoformat()


async def _login(client: AsyncClient, phone: str, password: str) -> dict[str, str]:
    resp = await client.post(f"{API}/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


@dataclass(slots=True)
class ClinicCtx:
    clinic_id: str
    headers: dict[str, str]
    patient_id: str
    service_id: str  # услуга 3000 сомов


async def _provision(admin_client: AsyncClient, client: AsyncClient, name: str) -> ClinicCtx:
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
    headers = await _login(client, phone, resp.json()["tempPassword"])
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Лечение", "priceTiyin": 3000}
    )
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пётр", "lastName": "Тестовый"},
    )
    return ClinicCtx(
        clinic_id=str(resp.json()["id"]),
        headers=headers,
        patient_id=str(patient.json()["id"]),
        service_id=str(service.json()["id"]),
    )


async def _add_doctor(
    client: AsyncClient, ctx: ClinicCtx, name: str
) -> tuple[str, dict[str, str]]:
    """Врач (роль worker: reports.view_own) — id членства и его токен."""
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    resp = await client.post(
        f"{API}/clinic/staff",
        headers=ctx.headers,
        json={"fullName": name, "phone": phone, "specializationId": spec},
    )
    assert resp.status_code == 201, resp.text
    worker = await _login(client, phone, str(resp.json()["tempPassword"]))
    return str(resp.json()["membershipId"]), worker


async def _appointment(client: AsyncClient, ctx: ClinicCtx, doctor: str) -> str:
    """Приём на 3000 сомов в рабочем слоте сегодняшнего дня (попадает во все пресеты)."""
    body: dict[str, Any] = {
        "patientId": ctx.patient_id,
        "doctorMembershipId": doctor,
        "scheduledAt": _slot(),
        "items": [{"serviceId": ctx.service_id, "teeth": [46]}],
    }
    resp = await client.post(f"{API}/clinic/appointments", headers=ctx.headers, json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _complete(
    client: AsyncClient, ctx: ClinicCtx, appointment_id: str, paid: int | None
) -> None:
    body = {"payment": {"amountTiyin": paid, "method": "cash"}} if paid else None
    resp = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers, json=body
    )
    assert resp.status_code == 200, resp.text


@pytest.fixture
async def clinic_a(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    return await _provision(admin_client, client, "Отчёты A")


@pytest.fixture
async def clinic_b(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    return await _provision(admin_client, client, "Отчёты B")


# ── 1. Сводка клиники ───────────────────────────────────────────────────────


async def test_summary_aggregates_money_and_appointments(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Сводка: выручка, начислено, долг, средний чек и пациенты."""
    doctor, _ = await _add_doctor(client, clinic_a, "Врач Сводка")
    paid_appt = await _appointment(client, clinic_a, doctor)
    await _complete(client, clinic_a, paid_appt, 3000)
    debt_appt = await _appointment(client, clinic_a, doctor)
    await _complete(client, clinic_a, debt_appt, None)

    resp = await client.get(f"{API}/clinic/reports/summary", headers=clinic_a.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revenue"]["incomeTiyin"] == 3000
    assert body["revenue"]["netTiyin"] == 3000
    assert body["chargedTiyin"] == 6000  # два завершённых приёма по 3000
    assert body["debtTiyin"] == 3000  # второй не оплачен
    assert body["averageCheckTiyin"] == 3000
    assert body["appointments"]["completed"] == 2
    assert body["patients"]["total"] == 1
    assert body["patients"]["new"] == 1
    assert body["timezone"] == "Asia/Bishkek"


async def test_summary_period_presets_accepted(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Пресеты 7d/1m/3m/all принимаются; свежие данные попадают в каждый."""
    doctor, _ = await _add_doctor(client, clinic_a, "Врач Период")
    appt = await _appointment(client, clinic_a, doctor)
    await _complete(client, clinic_a, appt, 3000)

    for preset in ("7d", "1m", "3m", "all"):
        resp = await client.get(
            f"{API}/clinic/reports/summary?period={preset}", headers=clinic_a.headers
        )
        assert resp.status_code == 200, f"{preset}: {resp.text}"
        assert resp.json()["revenue"]["netTiyin"] == 3000, preset

    # «За всё время» не имеет нижней границы.
    all_time = await client.get(
        f"{API}/clinic/reports/summary?period=all", headers=clinic_a.headers
    )
    assert all_time.json()["dateFrom"] is None


async def test_summary_custom_range_overrides_preset(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Явные from/to перекрывают пресет; прошлый период пуст."""
    doctor, _ = await _add_doctor(client, clinic_a, "Врач Диапазон")
    appt = await _appointment(client, clinic_a, doctor)
    await _complete(client, clinic_a, appt, 3000)

    past = await client.get(
        f"{API}/clinic/reports/summary?period=all&from=2020-01-01&to=2020-01-31",
        headers=clinic_a.headers,
    )
    assert past.status_code == 200, past.text
    assert past.json()["dateFrom"] == "2020-01-01"
    assert past.json()["revenue"]["netTiyin"] == 0
    assert past.json()["chargedTiyin"] == 0


# ── 2. RBAC: свои против всех ───────────────────────────────────────────────


async def test_doctor_sees_only_own_reports(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """reports.view_own: врач видит только свои приёмы, владелец — все."""
    mine, doctor_headers = await _add_doctor(client, clinic_a, "Врач Свой")
    other, _ = await _add_doctor(client, clinic_a, "Врач Чужой")

    my_appt = await _appointment(client, clinic_a, mine)
    await _complete(client, clinic_a, my_appt, 3000)
    other_appt = await _appointment(client, clinic_a, other)
    await _complete(client, clinic_a, other_appt, 3000)

    # Владелец видит обе выручки.
    owner = await client.get(f"{API}/clinic/reports/summary", headers=clinic_a.headers)
    assert owner.json()["revenue"]["netTiyin"] == 6000
    assert owner.json()["appointments"]["completed"] == 2

    # Врач — только свою.
    own = await client.get(f"{API}/clinic/reports/summary", headers=doctor_headers)
    assert own.status_code == 200, own.text
    assert own.json()["revenue"]["netTiyin"] == 3000
    assert own.json()["appointments"]["completed"] == 1
    assert own.json()["chargedTiyin"] == 3000


async def test_doctors_report_scoped_to_self_for_worker(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Отчёт по врачам: владелец видит всех, врач — одну строку (себя)."""
    mine, doctor_headers = await _add_doctor(client, clinic_a, "Врач Один")
    other, _ = await _add_doctor(client, clinic_a, "Врач Два")
    await _complete(client, clinic_a, await _appointment(client, clinic_a, mine), 3000)
    await _complete(client, clinic_a, await _appointment(client, clinic_a, other), None)

    owner = await client.get(f"{API}/clinic/reports/doctors", headers=clinic_a.headers)
    assert owner.status_code == 200, owner.text
    ids = {r["membershipId"] for r in owner.json()["rows"]}
    assert ids == {mine, other}

    own = await client.get(f"{API}/clinic/reports/doctors", headers=doctor_headers)
    assert own.status_code == 200, own.text
    rows = own.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["membershipId"] == mine
    assert rows[0]["chargedTiyin"] == 3000
    assert rows[0]["paidTiyin"] == 3000
    assert rows[0]["debtTiyin"] == 0

    # У неоплаченного врача в полном отчёте виден долг.
    other_row = next(r for r in owner.json()["rows"] if r["membershipId"] == other)
    assert other_row["debtTiyin"] == 3000


async def test_services_and_revenue_scoped_for_worker(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Услуги и динамика выручки тоже сужаются до своих приёмов."""
    mine, doctor_headers = await _add_doctor(client, clinic_a, "Врач Услуги")
    other, _ = await _add_doctor(client, clinic_a, "Врач Услуги 2")
    await _complete(client, clinic_a, await _appointment(client, clinic_a, mine), 3000)
    await _complete(client, clinic_a, await _appointment(client, clinic_a, other), 3000)

    services = await client.get(f"{API}/clinic/reports/services", headers=doctor_headers)
    assert services.status_code == 200, services.text
    assert services.json()["rows"][0]["count"] == 1  # только своя манипуляция
    assert services.json()["rows"][0]["revenueTiyin"] == 3000

    revenue = await client.get(
        f"{API}/clinic/reports/revenue?groupBy=day", headers=doctor_headers
    )
    assert revenue.status_code == 200, revenue.text
    assert sum(p["netTiyin"] for p in revenue.json()["points"]) == 3000

    owner_revenue = await client.get(
        f"{API}/clinic/reports/revenue?groupBy=month", headers=clinic_a.headers
    )
    assert sum(p["netTiyin"] for p in owner_revenue.json()["points"]) == 6000


# ── 3. Изоляция клиник ──────────────────────────────────────────────────────


async def test_reports_do_not_leak_across_clinics(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Отчёты клиники A не содержат денег и приёмов клиники B."""
    doctor_b, _ = await _add_doctor(client, clinic_b, "Врач B")
    await _complete(client, clinic_b, await _appointment(client, clinic_b, doctor_b), 3000)

    summary_a = await client.get(f"{API}/clinic/reports/summary", headers=clinic_a.headers)
    assert summary_a.json()["revenue"]["netTiyin"] == 0
    assert summary_a.json()["appointments"]["total"] == 0
    assert (await client.get(f"{API}/clinic/reports/doctors", headers=clinic_a.headers)).json()[
        "rows"
    ] == []


# ── 4. Платформенный контур Skrezo ──────────────────────────────────────────


async def test_admin_clinics_report_lists_all_with_metrics(
    admin_client: AsyncClient, client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Свод Skrezo: все клиники с выручкой и долгом, плюс итоги."""
    doctor_a, _ = await _add_doctor(client, clinic_a, "Врач A")
    await _complete(client, clinic_a, await _appointment(client, clinic_a, doctor_a), 3000)
    doctor_b, _ = await _add_doctor(client, clinic_b, "Врач B")
    await _complete(client, clinic_b, await _appointment(client, clinic_b, doctor_b), None)

    resp = await admin_client.get(f"{API}/admin/reports/clinics")
    assert resp.status_code == 200, resp.text
    rows = {r["clinicId"]: r for r in resp.json()["rows"]}
    assert rows[clinic_a.clinic_id]["revenueNetTiyin"] == 3000
    assert rows[clinic_a.clinic_id]["debtTiyin"] == 0
    assert rows[clinic_b.clinic_id]["revenueNetTiyin"] == 0
    assert rows[clinic_b.clinic_id]["debtTiyin"] == 3000
    assert rows[clinic_a.clinic_id]["doctors"] >= 1
    assert resp.json()["totalRevenueNetTiyin"] >= 3000


async def test_admin_can_open_any_clinic_report(
    admin_client: AsyncClient, client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Skrezo открывает полный набор отчётов по выбранной клинике."""
    doctor, _ = await _add_doctor(client, clinic_a, "Врач Админ")
    await _complete(client, clinic_a, await _appointment(client, clinic_a, doctor), 3000)
    base = f"{API}/admin/reports/clinics/{clinic_a.clinic_id}"

    summary = await admin_client.get(f"{base}/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["revenue"]["netTiyin"] == 3000

    for path in ("revenue", "doctors", "services"):
        resp = await admin_client.get(f"{base}/{path}")
        assert resp.status_code == 200, f"{path}: {resp.text}"

    doctors = await admin_client.get(f"{base}/doctors")
    assert {r["membershipId"] for r in doctors.json()["rows"]} == {doctor}


async def test_admin_reports_unknown_clinic_is_404(admin_client: AsyncClient) -> None:
    """Отчёт по несуществующей клинике → 404."""
    resp = await admin_client.get(f"{API}/admin/reports/clinics/{uuid.uuid4()}/summary")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "clinic_not_found"


async def test_clinic_staff_cannot_use_admin_reports(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Сотрудник клиники (даже владелец) не входит в платформенный контур."""
    for path in (
        "/admin/reports/clinics",
        f"/admin/reports/clinics/{clinic_a.clinic_id}/summary",
    ):
        resp = await client.get(f"{API}{path}", headers=clinic_a.headers)
        assert resp.status_code == 403, f"{path}: {resp.text}"
        assert resp.json()["error"] == "not_platform_admin"


@pytest.mark.parametrize(
    "path",
    [
        "/clinic/reports/summary",
        "/clinic/reports/revenue",
        "/clinic/reports/doctors",
        "/clinic/reports/services",
        "/admin/reports/clinics",
    ],
)
async def test_reports_without_token_return_401(client: AsyncClient, path: str) -> None:
    """Все ручки отчётов закрыты авторизацией."""
    resp = await client.get(f"{API}{path}")
    assert resp.status_code == 401, resp.text
