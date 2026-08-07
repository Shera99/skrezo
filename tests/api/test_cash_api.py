"""API-тесты кассы клиники: платежи, возвраты, долги, сводка и завершение приёма.

Ключевые риски:

* деньги — переплата запрещена, частичная оплата даёт долг, возврат возвращает
  долг; возврат частичный и многократный, но в сумме не больше платежа
  (сверх остатка — 422, по исчерпанному платежу — 409);
* изоляция клиник (IDOR) — платёж/возврат по чужому приёму/платежу не проходит;
* RBAC — без права/токена отказ; cash.view_own отдаёт только свои операции;
* завершение приёма и оплата атомарны: переплата откатывает завершение.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, working_slot

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070095{next(_phone_seq):04d}"


async def _login(client: AsyncClient, phone: str, password: str) -> dict[str, str]:
    resp = await client.post(f"{API}/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


@dataclass(slots=True)
class ClinicCtx:
    clinic_id: str
    headers: dict[str, str]
    patient_id: str
    service_id: str  # услуга на 3000 тыйынов


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
    assert service.status_code == 201, service.text
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пётр", "lastName": "Тестовый"},
    )
    assert patient.status_code == 201, patient.text
    return ClinicCtx(
        clinic_id=str(resp.json()["id"]),
        headers=headers,
        patient_id=str(patient.json()["id"]),
        service_id=str(service.json()["id"]),
    )


async def _add_worker(
    client: AsyncClient, ctx: ClinicCtx, name: str
) -> tuple[str, dict[str, str]]:
    """Работник (роль worker: cash.operate + cash.view_own) — id членства и его токен."""
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


async def _appointment(
    client: AsyncClient, ctx: ClinicCtx, *, doctor: str | None = None
) -> str:
    """Приём с ценой 3000 (одна услуга по зубу). Возвращает id."""
    body: dict[str, Any] = {
        "patientId": ctx.patient_id,
        "scheduledAt": working_slot(),
        "items": [{"serviceId": ctx.service_id, "teeth": [46]}],
    }
    if doctor is not None:
        body["doctorMembershipId"] = doctor
    resp = await client.post(f"{API}/clinic/appointments", headers=ctx.headers, json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["priceTotalTiyin"] == 3000
    return str(resp.json()["id"])


@pytest.fixture
async def clinic_a(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    return await _provision(admin_client, client, "Касса A")


@pytest.fixture
async def clinic_b(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    return await _provision(admin_client, client, "Касса B")


def _pay(appointment_id: str, amount: int, method: str = "cash") -> dict[str, Any]:
    return {"appointmentId": appointment_id, "amountTiyin": amount, "method": method}


# ── 1. Завершение приёма с оплатой ──────────────────────────────────────────


async def test_complete_with_payment_closes_appointment_and_debt(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Завершение с полной оплатой: статус completed, платёж в кассе, долга нет."""
    appt = await _appointment(client, clinic_a)
    done = await client.post(
        f"{API}/clinic/appointments/{appt}/complete",
        headers=clinic_a.headers,
        json={"payment": {"amountTiyin": 3000, "method": "cash"}},
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed"

    payments = await client.get(f"{API}/clinic/cash/payments", headers=clinic_a.headers)
    assert payments.status_code == 200, payments.text
    assert payments.json()["totalCount"] == 1
    assert payments.json()["list"][0]["amountTiyin"] == 3000

    debts = await client.get(f"{API}/clinic/cash/debts", headers=clinic_a.headers)
    assert debts.json()["items"] == []
    assert debts.json()["totalOutstandingTiyin"] == 0


async def test_complete_without_payment_leaves_debt(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Завершение без оплаты: приём закрыт, остаток становится долгом."""
    appt = await _appointment(client, clinic_a)
    done = await client.post(
        f"{API}/clinic/appointments/{appt}/complete", headers=clinic_a.headers
    )
    assert done.status_code == 200, done.text

    debts = await client.get(f"{API}/clinic/cash/debts", headers=clinic_a.headers)
    items = debts.json()["items"]
    assert len(items) == 1
    assert items[0]["appointmentId"] == appt
    assert items[0]["outstandingTiyin"] == 3000
    assert debts.json()["totalOutstandingTiyin"] == 3000


async def test_complete_twice_is_409_and_cancelled_cannot_complete(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Повторное завершение → 409; отменённый приём завершить нельзя → 422."""
    appt = await _appointment(client, clinic_a)
    assert (
        await client.post(f"{API}/clinic/appointments/{appt}/complete", headers=clinic_a.headers)
    ).status_code == 200
    again = await client.post(
        f"{API}/clinic/appointments/{appt}/complete", headers=clinic_a.headers
    )
    assert again.status_code == 409, again.text
    assert again.json()["error"] == "already_completed"

    other = await _appointment(client, clinic_a)
    await client.patch(
        f"{API}/clinic/appointments/{other}", headers=clinic_a.headers,
        json={"status": "cancelled", "cancelReason": "patient_request"},
    )
    cancelled = await client.post(
        f"{API}/clinic/appointments/{other}/complete", headers=clinic_a.headers
    )
    assert cancelled.status_code == 422, cancelled.text
    assert cancelled.json()["error"] == "cannot_complete_cancelled"


async def test_complete_overpayment_rolls_back_completion(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Переплата при завершении откатывает и статус: приём остаётся незавершённым."""
    appt = await _appointment(client, clinic_a)
    resp = await client.post(
        f"{API}/clinic/appointments/{appt}/complete",
        headers=clinic_a.headers,
        json={"payment": {"amountTiyin": 5000, "method": "cash"}},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "overpayment"

    detail = await client.get(f"{API}/clinic/appointments/{appt}", headers=clinic_a.headers)
    assert detail.json()["status"] != "completed"
    # Платёж тоже не записан.
    assert (
        await client.get(f"{API}/clinic/cash/payments", headers=clinic_a.headers)
    ).json()["totalCount"] == 0


# ── 2. Платежи, долги, переплата ────────────────────────────────────────────


async def test_partial_payments_reduce_debt_to_zero(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Частичные оплаты уменьшают долг; полная сумма закрывает его."""
    appt = await _appointment(client, clinic_a)
    first = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 1000)
    )
    assert first.status_code == 201, first.text

    debts = await client.get(
        f"{API}/clinic/cash/debts?patientId={clinic_a.patient_id}", headers=clinic_a.headers
    )
    assert debts.json()["items"] == []  # приём ещё не завершён — не долг

    await client.post(f"{API}/clinic/appointments/{appt}/complete", headers=clinic_a.headers)
    after_complete = await client.get(f"{API}/clinic/cash/debts", headers=clinic_a.headers)
    assert after_complete.json()["items"][0]["outstandingTiyin"] == 2000

    second = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 2000, "card")
    )
    assert second.status_code == 201, second.text
    assert (
        await client.get(f"{API}/clinic/cash/debts", headers=clinic_a.headers)
    ).json()["items"] == []


async def test_overpayment_and_nothing_to_pay_rejected(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Сумма больше остатка → 422 overpayment; по оплаченному приёму → nothing_to_pay."""
    appt = await _appointment(client, clinic_a)
    over = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 4000)
    )
    assert over.status_code == 422, over.text
    assert over.json()["error"] == "overpayment"
    assert over.json()["outstandingTiyin"] == 3000

    assert (
        await client.post(
            f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 3000)
        )
    ).status_code == 201
    nothing = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 1)
    )
    assert nothing.status_code == 422, nothing.text
    assert nothing.json()["error"] == "nothing_to_pay"


async def test_summary_totals_by_method(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """Сводка: приход разбит по способам оплаты, нетто = приход − возвраты."""
    appt = await _appointment(client, clinic_a)
    await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 1000, "cash")
    )
    await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 2000, "card")
    )
    summary = await client.get(f"{API}/clinic/cash/summary", headers=clinic_a.headers)
    assert summary.status_code == 200, summary.text
    body = summary.json()
    by_method = {m["method"]: m for m in body["byMethod"]}
    assert by_method["cash"]["incomeTiyin"] == 1000
    assert by_method["card"]["incomeTiyin"] == 2000
    assert body["totalIncomeTiyin"] == 3000
    assert body["netTiyin"] == 3000


# ── 3. Возвраты ─────────────────────────────────────────────────────────────


async def test_refund_returns_debt_and_double_refund_conflicts(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Возврат возвращает долг; когда возвращать нечего — 409."""
    appt = await _appointment(client, clinic_a)
    await client.post(f"{API}/clinic/appointments/{appt}/complete", headers=clinic_a.headers)
    paid = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 3000)
    )
    payment_id = paid.json()["id"]
    assert (await client.get(f"{API}/clinic/cash/debts", headers=clinic_a.headers)).json()[
        "items"
    ] == []

    refund = await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund", headers=clinic_a.headers
    )
    assert refund.status_code == 201, refund.text
    assert refund.json()["kind"] == "refund"
    assert refund.json()["amountTiyin"] == 3000

    debts = await client.get(f"{API}/clinic/cash/debts", headers=clinic_a.headers)
    assert debts.json()["items"][0]["outstandingTiyin"] == 3000

    again = await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund", headers=clinic_a.headers
    )
    assert again.status_code == 409, again.text
    assert again.json()["error"] == "already_refunded"


async def test_partial_refunds_accumulate_to_full(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """«Из трёх услуг переделали одну»: возврат частичный и не последний.

    Долг растёт на каждую возвращённую часть, остаток к возврату уменьшается,
    а когда он исчерпан — следующий возврат уже 409.
    """
    appt = await _appointment(client, clinic_a)
    await client.post(f"{API}/clinic/appointments/{appt}/complete", headers=clinic_a.headers)
    paid = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 3000)
    )
    payment_id = paid.json()["id"]

    first = await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund",
        headers=clinic_a.headers,
        json={"amountTiyin": 1000, "comment": "Переделали чистку"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["amountTiyin"] == 1000
    assert first.json()["refundsPaymentId"] == payment_id
    assert first.json()["comment"] == "Переделали чистку"

    debts = await client.get(f"{API}/clinic/cash/debts", headers=clinic_a.headers)
    assert debts.json()["items"][0]["outstandingTiyin"] == 1000

    second = await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund",
        headers=clinic_a.headers,
        json={"amountTiyin": 2000},
    )
    assert second.status_code == 201, second.text

    debts = await client.get(f"{API}/clinic/cash/debts", headers=clinic_a.headers)
    assert debts.json()["items"][0]["outstandingTiyin"] == 3000

    exhausted = await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund",
        headers=clinic_a.headers,
        json={"amountTiyin": 1},
    )
    assert exhausted.status_code == 409, exhausted.text
    assert exhausted.json()["error"] == "already_refunded"


async def test_refund_over_remainder_is_rejected(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Вернуть больше остатка нельзя — в ошибке видно, сколько можно."""
    appt = await _appointment(client, clinic_a)
    paid = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 3000)
    )
    payment_id = paid.json()["id"]

    over = await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund",
        headers=clinic_a.headers,
        json={"amountTiyin": 3001},
    )
    assert over.status_code == 422, over.text
    assert over.json()["error"] == "refund_exceeds_payment"
    assert over.json()["refundableTiyin"] == 3000

    await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund",
        headers=clinic_a.headers,
        json={"amountTiyin": 2500},
    )
    over_again = await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund",
        headers=clinic_a.headers,
        json={"amountTiyin": 600},
    )
    assert over_again.status_code == 422, over_again.text
    assert over_again.json()["refundableTiyin"] == 500


async def test_refund_without_amount_returns_remainder(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Без суммы возвращается остаток — привычный «полный возврат» цел."""
    appt = await _appointment(client, clinic_a)
    paid = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 3000)
    )
    payment_id = paid.json()["id"]
    await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund",
        headers=clinic_a.headers,
        json={"amountTiyin": 1200},
    )

    rest = await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund", headers=clinic_a.headers
    )
    assert rest.status_code == 201, rest.text
    assert rest.json()["amountTiyin"] == 1800


async def test_journal_shows_refundable_remainder(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Журнал сам отвечает, сколько ещё можно вернуть — фронт не считает за нас."""
    appt = await _appointment(client, clinic_a)
    paid = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 3000)
    )
    payment_id = paid.json()["id"]
    assert paid.json()["refundableTiyin"] == 3000
    assert paid.json()["refundedTiyin"] == 0

    await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund",
        headers=clinic_a.headers,
        json={"amountTiyin": 1000},
    )
    journal = await client.get(f"{API}/clinic/cash/payments", headers=clinic_a.headers)
    rows = {r["id"]: r for r in journal.json()["list"]}
    assert rows[payment_id]["refundedTiyin"] == 1000
    assert rows[payment_id]["refundableTiyin"] == 2000
    # У самого возврата возвращать нечего.
    refund_row = next(r for r in rows.values() if r["kind"] == "refund")
    assert refund_row["refundableTiyin"] == 0
    assert refund_row["refundsPaymentId"] == payment_id


async def test_refund_of_refund_is_rejected(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """Возврат по возврату — не операция кассы: сторнируют приход."""
    appt = await _appointment(client, clinic_a)
    paid = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt, 3000)
    )
    refund = await client.post(
        f"{API}/clinic/cash/payments/{paid.json()['id']}/refund",
        headers=clinic_a.headers,
        json={"amountTiyin": 1000},
    )
    resp = await client.post(
        f"{API}/clinic/cash/payments/{refund.json()['id']}/refund", headers=clinic_a.headers
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "not_a_payment"


async def test_refund_unknown_payment_is_404(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Возврат несуществующего платежа → 404."""
    resp = await client.post(
        f"{API}/clinic/cash/payments/{uuid.uuid4()}/refund", headers=clinic_a.headers
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "payment_not_found"


# ── 4. Изоляция клиник (IDOR) ───────────────────────────────────────────────


async def test_payment_for_appointment_of_other_clinic_is_404(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Оплата приёма ЧУЖОЙ клиники не проходит."""
    appt_b = await _appointment(client, clinic_b)
    resp = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt_b, 1000)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "appointment_not_found"


async def test_refund_payment_of_other_clinic_is_404(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Возврат платежа ЧУЖОЙ клиники не проходит, платёж остаётся."""
    appt_b = await _appointment(client, clinic_b)
    paid = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_b.headers, json=_pay(appt_b, 1000)
    )
    payment_id = paid.json()["id"]
    attack = await client.post(
        f"{API}/clinic/cash/payments/{payment_id}/refund", headers=clinic_a.headers
    )
    assert attack.status_code == 404, attack.text
    # Журнал клиники B по-прежнему содержит платёж (возврата нет).
    journal = await client.get(f"{API}/clinic/cash/payments", headers=clinic_b.headers)
    assert journal.json()["totalCount"] == 1


async def test_debts_do_not_leak_across_clinics(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Долги клиники A не видят приёмов клиники B."""
    appt_b = await _appointment(client, clinic_b)
    await client.post(f"{API}/clinic/appointments/{appt_b}/complete", headers=clinic_b.headers)
    debts_a = await client.get(f"{API}/clinic/cash/debts", headers=clinic_a.headers)
    assert debts_a.json()["items"] == []


# ── 5. RBAC ─────────────────────────────────────────────────────────────────


async def test_worker_view_own_sees_only_own_payments(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """cash.view_own: работник видит только свои операции; владелец — все."""
    _, worker = await _add_worker(client, clinic_a, "Кассир")
    appt_owner = await _appointment(client, clinic_a)
    appt_worker = await _appointment(client, clinic_a)

    owner_pay = await client.post(
        f"{API}/clinic/cash/payments", headers=clinic_a.headers, json=_pay(appt_owner, 1000)
    )
    assert owner_pay.status_code == 201, owner_pay.text
    worker_pay = await client.post(
        f"{API}/clinic/cash/payments", headers=worker, json=_pay(appt_worker, 2000)
    )
    assert worker_pay.status_code == 201, worker_pay.text

    worker_journal = await client.get(f"{API}/clinic/cash/payments", headers=worker)
    assert worker_journal.status_code == 200, worker_journal.text
    ids = {x["id"] for x in worker_journal.json()["list"]}
    assert ids == {worker_pay.json()["id"]}

    owner_journal = await client.get(f"{API}/clinic/cash/payments", headers=clinic_a.headers)
    assert owner_journal.json()["totalCount"] == 2

    # Своя сводка работника — только его 2000.
    worker_summary = await client.get(f"{API}/clinic/cash/summary", headers=worker)
    assert worker_summary.json()["totalIncomeTiyin"] == 2000


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/clinic/cash/payments"),
        ("POST", "/clinic/cash/payments"),
        ("GET", "/clinic/cash/summary"),
        ("GET", "/clinic/cash/debts"),
        ("POST", f"/clinic/cash/payments/{uuid.uuid4()}/refund"),
    ],
)
async def test_cash_endpoints_without_token_return_401(
    client: AsyncClient, method: str, path: str
) -> None:
    """Все ручки кассы закрыты авторизацией."""
    resp = await client.request(method, f"{API}{path}", json={})
    assert resp.status_code == 401, resp.text
