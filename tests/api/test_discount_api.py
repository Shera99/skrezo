"""API-тесты скидки: право `cash.discount` и потолок роли.

Скидка — единственная операция, где сотрудник в одиночку уменьшает выручку,
поэтому проверяется именно контроль над ней:

* без права скидку не дать вовсе (403), с правом — только в пределах потолка
  роли (владелец 100, администратор 20, врач 0);
* итог к оплате = сумма позиций − скидка, и долг кассы считается по нему;
* скидка не может опустить итог ниже уже оплаченного — иначе касса уходит в
  минус мимо возвратов;
* завершённый приём — финансовый факт: скидку на него уже не дают;
* каждая скидка попадает в аудит: владелец должен видеть, кто и сколько скинул.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, today_slot, working_slot

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070098{next(_phone_seq):04d}"


async def _login(client: AsyncClient, phone: str, password: str) -> dict[str, str]:
    resp = await client.post(f"{API}/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


@dataclass(slots=True)
class ClinicCtx:
    headers: dict[str, str]  # владелец: потолок 100
    worker_headers: dict[str, str]  # врач: права на скидку нет
    patient_id: str
    service_id: str  # услуга на 10000 тыйынов


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Скидка",
            "adminPhone": phone,
            "name": "Клиника Скидка",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await _login(client, phone, created.json()["tempPassword"])

    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Скидкин"},
    )
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Лечение", "priceTiyin": 10000}
    )
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    worker_phone = _phone()
    worker = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": "Врач", "phone": worker_phone, "specializationId": spec},
    )
    assert worker.status_code == 201, worker.text
    worker_headers = await _login(client, worker_phone, str(worker.json()["tempPassword"]))

    return ClinicCtx(
        headers=headers,
        worker_headers=worker_headers,
        patient_id=str(patient.json()["id"]),
        service_id=str(service.json()["id"]),
    )


async def _book(
    client: AsyncClient, ctx: ClinicCtx, quantity: int = 1, *, when: str | None = None
) -> str:
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "scheduledAt": when or working_slot(),
            "items": [{"serviceId": ctx.service_id, "teeth": [46], "quantity": quantity}],
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _discount(
    client: AsyncClient,
    ctx: ClinicCtx,
    appointment_id: str,
    percent: int,
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    return await client.put(
        f"{API}/clinic/appointments/{appointment_id}/discount",
        headers=headers or ctx.headers,
        json={"percent": percent},
    )


# ── 1. Расчёт ───────────────────────────────────────────────────────────────


async def test_discount_reduces_total(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Итог к оплате = сумма позиций минус скидка; обе величины видны в чеке."""
    appointment_id = await _book(client, ctx)
    resp = await _discount(client, ctx, appointment_id, 10)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subtotalTiyin"] == 10000
    assert body["discountPercent"] == 10
    assert body["discountTiyin"] == 1000
    assert body["priceTotalTiyin"] == 9000


async def test_zero_percent_removes_discount(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Ноль снимает скидку и возвращает полный итог."""
    appointment_id = await _book(client, ctx)
    await _discount(client, ctx, appointment_id, 15)

    resp = await _discount(client, ctx, appointment_id, 0)
    assert resp.status_code == 200, resp.text
    assert resp.json()["discountTiyin"] == 0
    assert resp.json()["priceTotalTiyin"] == 10000


async def test_discount_survives_items_change(client: AsyncClient, ctx: ClinicCtx) -> None:
    """После правки чека скидка относится к НОВОЙ сумме, а не к прежней."""
    appointment_id = await _book(client, ctx)
    await _discount(client, ctx, appointment_id, 10)

    resp = await client.put(
        f"{API}/clinic/appointments/{appointment_id}/items",
        headers=ctx.headers,
        json={"items": [{"serviceId": ctx.service_id, "teeth": [46], "quantity": 2}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["subtotalTiyin"] == 20000
    assert resp.json()["discountTiyin"] == 2000
    assert resp.json()["priceTotalTiyin"] == 18000


async def test_debt_counts_discounted_total(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Касса берёт долг с итога СО скидкой — иначе пациенту выставят полную цену."""
    appointment_id = await _book(client, ctx)
    await _discount(client, ctx, appointment_id, 20)
    done = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers
    )
    assert done.status_code == 200, done.text

    debts = await client.get(f"{API}/clinic/cash/debts", headers=ctx.headers)
    assert debts.json()["items"][0]["outstandingTiyin"] == 8000


# ── 2. Право и потолок роли ─────────────────────────────────────────────────


async def test_worker_without_permission_is_403(client: AsyncClient, ctx: ClinicCtx) -> None:
    """У врача права на скидку нет — чек он не уменьшит."""
    appointment_id = await _book(client, ctx)
    resp = await _discount(client, ctx, appointment_id, 5, headers=ctx.worker_headers)
    assert resp.status_code == 403, resp.text


async def test_ceiling_limits_discount(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Роль с потолком 20% не даёт 30% — и в ошибке видно, сколько можно."""
    roles = await client.get(f"{API}/clinic/roles", headers=ctx.headers)
    manager = next(r for r in roles.json()["items"] if r["key"] == "manager")
    assert manager["maxDiscountPercent"] == 20

    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    staff = await client.post(
        f"{API}/clinic/staff",
        headers=ctx.headers,
        json={
            "fullName": "Администратор",
            "phone": phone,
            "specializationId": spec,
            "roleId": manager["id"],
        },
    )
    assert staff.status_code == 201, staff.text
    manager_headers = await _login(client, phone, str(staff.json()["tempPassword"]))

    appointment_id = await _book(client, ctx)
    over = await _discount(client, ctx, appointment_id, 30, headers=manager_headers)
    assert over.status_code == 403, over.text
    assert over.json()["error"] == "discount_limit_exceeded"
    assert over.json()["maxDiscountPercent"] == 20

    ok = await _discount(client, ctx, appointment_id, 20, headers=manager_headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["priceTotalTiyin"] == 8000


async def test_owner_may_discount_fully(client: AsyncClient, ctx: ClinicCtx) -> None:
    """У владельца потолок 100: он вправе обнулить чек — это его решение."""
    appointment_id = await _book(client, ctx)
    resp = await _discount(client, ctx, appointment_id, 100)
    assert resp.status_code == 200, resp.text
    assert resp.json()["priceTotalTiyin"] == 0


async def test_ceiling_is_editable_per_clinic(client: AsyncClient, ctx: ClinicCtx) -> None:
    """«У нас администратору можно 10, а не 20» — потолок настраивается ролью."""
    roles = await client.get(f"{API}/clinic/roles", headers=ctx.headers)
    manager = next(r for r in roles.json()["items"] if r["key"] == "manager")

    resp = await client.patch(
        f"{API}/clinic/roles/{manager['id']}",
        headers=ctx.headers,
        json={"maxDiscountPercent": 10},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["maxDiscountPercent"] == 10


async def test_discount_is_audited(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Кто и сколько скинул — видно в журнале КЛИНИКИ, а не Skrezo.

    Скидка — операция сотрудника, и разбирает её владелец клиники; в
    платформенном журнале ей делать нечего (см. ``test_clinic_audit_api``).
    """
    appointment_id = await _book(client, ctx)
    assert (await _discount(client, ctx, appointment_id, 25)).status_code == 200

    audit = await client.get(f"{API}/clinic/audit", headers=ctx.headers)
    assert audit.status_code == 200, audit.text
    assert "appointment.discount" in audit.text


# ── 3. Границы ──────────────────────────────────────────────────────────────


async def test_discount_below_paid_is_rejected(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Скидка не опускает итог ниже оплаченного — сначала возврат."""
    appointment_id = await _book(client, ctx)
    paid = await client.post(
        f"{API}/clinic/cash/payments",
        headers=ctx.headers,
        json={"appointmentId": appointment_id, "amountTiyin": 9000, "method": "cash"},
    )
    assert paid.status_code == 201, paid.text

    resp = await _discount(client, ctx, appointment_id, 50)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "total_below_paid"
    assert resp.json()["paidTiyin"] == 9000

    card = await client.get(f"{API}/clinic/appointments/{appointment_id}", headers=ctx.headers)
    assert card.json()["priceTotalTiyin"] == 10000


async def test_discount_on_completed_is_409(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Завершённый приём — финансовый факт: скидку на него уже не дают."""
    appointment_id = await _book(client, ctx)
    await client.post(f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers)

    resp = await _discount(client, ctx, appointment_id, 10)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "appointment_completed"


async def test_percent_over_hundred_is_422(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Скидка больше 100% — не скидка."""
    appointment_id = await _book(client, ctx)
    resp = await _discount(client, ctx, appointment_id, 120)
    assert resp.status_code == 422, resp.text


async def test_discounts_visible_in_summary(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Сколько клиника раздала скидками — видно в отчёте."""
    # Отчёт за период: приём должен попасть в него, поэтому дата — сегодняшняя.
    appointment_id = await _book(client, ctx, when=today_slot())
    await _discount(client, ctx, appointment_id, 10)
    await client.post(f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers)

    summary = await client.get(f"{API}/clinic/reports/summary", headers=ctx.headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["discountsTiyin"] == 1000
    assert summary.json()["chargedTiyin"] == 9000
