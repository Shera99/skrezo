"""Списки кабинета: пациенты с последним визитом и долгом, приёмы с врачом и телефоном.

Этап 1 запросов фронта (К-1, К-2, К-4, К-5):

* у пациента в списке — последний завершённый приём и долг в ЭТОЙ клинике,
  долг по правилам кассы (остаток = цена − платежи + возвраты);
* размер страницы пациентов задаётся ``pageSize`` (1–100);
* у приёма в списке — ``doctorMembershipId`` и ``patientPhone``;
* ``to`` без времени — день целиком в поясе клиники, время без пояса — пояс
  клиники, мусор — 422 ``invalid_period``.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, otp_login, own_membership_id, working_slot

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070097{next(_phone_seq):04d}"


@dataclass(slots=True)
class Ctx:
    headers: dict[str, str]
    doctor_id: str
    service_id: str  # услуга на 3000 тыйынов


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> Ctx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Списки",
            "adminPhone": phone,
            "name": "Клиника Списки",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(client, phone)
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Лечение", "priceTiyin": 3000}
    )
    assert service.status_code == 201, service.text
    return Ctx(
        headers=headers,
        doctor_id=await own_membership_id(client, headers),
        service_id=str(service.json()["id"]),
    )


async def _patient(client: AsyncClient, ctx: Ctx, first_name: str) -> dict[str, Any]:
    """Пациент клиники: id и телефон в E.164 (так его отдают списки)."""
    phone = _phone()
    resp = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.headers,
        json={"phone": phone, "firstName": first_name, "lastName": "Тестова"},
    )
    assert resp.status_code == 201, resp.text
    return {"id": resp.json()["id"], "phone": f"+996{phone[1:]}"}


async def _appointment(
    client: AsyncClient, ctx: Ctx, patient_id: str, slot: str | None = None
) -> dict[str, Any]:
    """Приём на 3000 тыйынов: id и слот, на который записали."""
    slot = slot or working_slot()
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": patient_id,
            "doctorMembershipId": ctx.doctor_id,
            "scheduledAt": slot,
            "items": [{"serviceId": ctx.service_id, "teeth": [46]}],
        },
    )
    assert resp.status_code == 201, resp.text
    return {"id": resp.json()["id"], "scheduledAt": slot}


async def _complete(client: AsyncClient, ctx: Ctx, appointment_id: str, paid: int) -> None:
    body: dict[str, Any] = {}
    if paid:
        body = {"payment": {"amountTiyin": paid, "method": "cash"}}
    done = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers, json=body
    )
    assert done.status_code == 200, done.text


async def _patients(client: AsyncClient, ctx: Ctx, **params: Any) -> Any:
    resp = await client.get(f"{API}/clinic/patients", headers=ctx.headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Пациенты ──────────────────────────────────────────────────────────────────


async def test_patient_list_shows_last_visit_and_debt(client: AsyncClient, ctx: Ctx) -> None:
    debtor = await _patient(client, ctx, "Должник")
    paid_up = await _patient(client, ctx, "Оплатил")
    newcomer = await _patient(client, ctx, "Новичок")

    # Должник: завершённый приём оплачен на 1000 из 3000, следующий ещё впереди.
    visit = await _appointment(client, ctx, debtor["id"])
    await _complete(client, ctx, visit["id"], paid=1000)
    await _appointment(client, ctx, debtor["id"])
    # Оплатил: завершён и оплачен целиком. Новичок: только будущий приём.
    settled = await _appointment(client, ctx, paid_up["id"])
    await _complete(client, ctx, settled["id"], paid=3000)
    await _appointment(client, ctx, newcomer["id"])

    rows = {p["id"]: p for p in (await _patients(client, ctx))["list"]}
    assert rows[debtor["id"]]["debtTiyin"] == 2000
    assert rows[debtor["id"]]["lastVisitAt"] is not None
    assert datetime.fromisoformat(rows[debtor["id"]]["lastVisitAt"]) == datetime.fromisoformat(
        visit["scheduledAt"]
    )
    assert rows[paid_up["id"]]["debtTiyin"] == 0 and rows[paid_up["id"]]["lastVisitAt"]
    assert rows[newcomer["id"]] | {"debtTiyin": 0, "lastVisitAt": None} == rows[newcomer["id"]]

    # Возврат по оплаченному приёму возвращает долг — как в кассе.
    payments = await client.get(f"{API}/clinic/cash/payments", headers=ctx.headers)
    refund_of = next(
        p["id"] for p in payments.json()["list"] if p["appointmentId"] == settled["id"]
    )
    refund = await client.post(
        f"{API}/clinic/cash/payments/{refund_of}/refund", headers=ctx.headers
    )
    assert refund.status_code == 201, refund.text
    rows = {p["id"]: p for p in (await _patients(client, ctx))["list"]}
    assert rows[paid_up["id"]]["debtTiyin"] == 3000


async def test_patient_list_page_size(client: AsyncClient, ctx: Ctx) -> None:
    for name in ("Первый", "Второй", "Третий"):
        patient = await _patient(client, ctx, name)
        await _appointment(client, ctx, patient["id"])

    page = await _patients(client, ctx, pageSize=2)
    assert len(page["list"]) == 2 and page["pageSize"] == 2 and page["totalCount"] == 3
    assert len((await _patients(client, ctx, pageSize=2, page=2))["list"]) == 1
    assert (await _patients(client, ctx))["pageSize"] == 25  # по умолчанию как раньше
    for bad in (0, 101):
        resp = await client.get(
            f"{API}/clinic/patients", headers=ctx.headers, params={"pageSize": bad}
        )
        assert resp.status_code == 422


# ── Приёмы ────────────────────────────────────────────────────────────────────


async def _appointments(client: AsyncClient, ctx: Ctx, **params: Any) -> list[str]:
    resp = await client.get(f"{API}/clinic/appointments", headers=ctx.headers, params=params)
    assert resp.status_code == 200, resp.text
    return [a["id"] for a in resp.json()["list"]]


async def test_appointment_list_row_has_doctor_and_patient_phone(
    client: AsyncClient, ctx: Ctx
) -> None:
    patient = await _patient(client, ctx, "Айгүл")
    created = await _appointment(client, ctx, patient["id"])
    resp = await client.get(f"{API}/clinic/appointments", headers=ctx.headers)
    row = next(a for a in resp.json()["list"] if a["id"] == created["id"])
    assert row["doctorMembershipId"] == ctx.doctor_id
    assert row["patientPhone"] == patient["phone"]


async def test_date_without_time_means_the_whole_day_in_clinic_timezone(
    client: AsyncClient, ctx: Ctx
) -> None:
    patient = await _patient(client, ctx, "Периодова")
    slot = working_slot()  # «ГГГГ-ММ-ДДTЧЧ:00:00+06:00» в рабочие часы клиники
    day, hour = date.fromisoformat(slot[:10]), int(slot[11:13])
    appointment = (await _appointment(client, ctx, patient["id"], slot))["id"]
    previous = (day - timedelta(days=1)).isoformat()

    # Дата без времени — день целиком: раньше to=день обрывался на полуночи.
    assert appointment in await _appointments(client, ctx, to=day.isoformat())
    assert appointment in await _appointments(
        client, ctx, **{"from": day.isoformat(), "to": day.isoformat()}
    )
    assert appointment not in await _appointments(client, ctx, to=previous)
    assert appointment not in await _appointments(
        client, ctx, **{"from": (day + timedelta(days=1)).isoformat()}
    )
    # Время без пояса — в поясе клиники (Бишкек), граница включительно.
    assert appointment not in await _appointments(
        client, ctx, to=f"{day.isoformat()}T{hour - 1:02d}:59:00"
    )
    assert appointment in await _appointments(client, ctx, to=f"{day.isoformat()}T{hour:02d}:00:00")
    assert appointment in await _appointments(client, ctx, to=slot)

    bad = await client.get(
        f"{API}/clinic/appointments", headers=ctx.headers, params={"to": "30.09.2026"}
    )
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_period"
