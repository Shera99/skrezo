"""API-тесты позиций приёма (чек): количество, зубы, правка чека.

Приём — список позиций, а не одна услуга. Отсюда риски, которые здесь закрыты:

* итог обязан считаться как Σ(цена за единицу × количество) — иначе чек занижен
  на каждой позиции с количеством больше одного;
* позиция без зуба (чистка, консультация) должна существовать: раньше её было
  некуда положить, и работа не попадала в чек;
* одна позиция на несколько зубов (мост) обязана остаться в истории **каждого**
  зуба — привязка работ к зубам не должна теряться;
* чек правится, пока приём идёт, и перестаёт — как только он закрыт: завершённый
  приём это финансовый факт, его меняют возвратом;
* правка не должна опускать итог ниже уже оплаченного — иначе в кассе появится
  приём с отрицательным долгом.
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
    return f"070097{next(_phone_seq):04d}"


async def _login(client: AsyncClient, phone: str, password: str) -> dict[str, str]:
    resp = await client.post(f"{API}/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


@dataclass(slots=True)
class ClinicCtx:
    headers: dict[str, str]
    patient_id: str
    filling_id: str  # «Пломба», 3000 тыйынов
    cleaning_id: str  # «Чистка», 1000 тыйынов — работа без зуба


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Чек",
            "adminPhone": phone,
            "name": "Клиника Чек",
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
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Чеков"},
    )
    assert patient.status_code == 201, patient.text
    filling = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Пломба", "priceTiyin": 3000}
    )
    cleaning = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Чистка", "priceTiyin": 1000}
    )
    return ClinicCtx(
        headers=headers,
        patient_id=str(patient.json()["id"]),
        filling_id=str(filling.json()["id"]),
        cleaning_id=str(cleaning.json()["id"]),
    )


async def _book(
    client: AsyncClient, ctx: ClinicCtx, items: list[dict[str, Any]], *, when: str | None = None
) -> str:
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "scheduledAt": when or working_slot(),
            "items": items,
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _card(client: AsyncClient, ctx: ClinicCtx, appointment_id: str) -> Any:
    resp = await client.get(f"{API}/clinic/appointments/{appointment_id}", headers=ctx.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1. Многопозиционный чек ─────────────────────────────────────────────────


async def test_multiple_items_sum_up(client: AsyncClient, ctx: ClinicCtx) -> None:
    """«Кариес 36 + чистка» — обычный приём: две позиции, итог их сумма."""
    appointment_id = await _book(
        client,
        ctx,
        [
            {"serviceId": ctx.filling_id, "teeth": [36]},
            {"serviceId": ctx.cleaning_id},
        ],
    )
    card = await _card(client, ctx, appointment_id)
    assert card["priceTotalTiyin"] == 4000
    assert len(card["items"]) == 2


async def test_quantity_multiplies_item_total(client: AsyncClient, ctx: ClinicCtx) -> None:
    """«Снимок ×3» — одна позиция: итог строки = цена × количество."""
    appointment_id = await _book(
        client, ctx, [{"serviceId": ctx.cleaning_id, "quantity": 3}]
    )
    card = await _card(client, ctx, appointment_id)
    item = card["items"][0]
    assert item["quantity"] == 3
    assert item["priceTiyin"] == 1000  # цена за единицу
    assert item["totalTiyin"] == 3000
    assert card["priceTotalTiyin"] == 3000


async def test_item_without_tooth_is_allowed(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Чистка к зубу не привязана — и всё равно попадает в чек."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.cleaning_id}])
    card = await _card(client, ctx, appointment_id)
    assert card["items"][0]["teeth"] == []
    assert card["priceTotalTiyin"] == 1000


async def test_item_with_several_teeth_lands_in_every_tooth_history(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Мост на 35–37 — одна работа с одной ценой, но история у каждого зуба."""
    await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [35, 36, 37]}])

    for tooth in (35, 36, 37):
        hist = await client.get(
            f"{API}/clinic/patients/{ctx.patient_id}/teeth/{tooth}", headers=ctx.headers
        )
        assert hist.status_code == 200, hist.text
        assert [r["serviceName"] for r in hist.json()["records"]] == ["Пломба"]


async def test_duplicate_tooth_in_item_is_stored_once(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Дубль зуба внутри позиции не размножает связку."""
    appointment_id = await _book(
        client, ctx, [{"serviceId": ctx.filling_id, "teeth": [36, 36]}]
    )
    card = await _card(client, ctx, appointment_id)
    assert card["items"][0]["teeth"] == [36]


async def test_invalid_tooth_in_item_is_422(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Номер не по FDI не проходит — как и раньше."""
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "scheduledAt": working_slot(),
            "items": [{"teeth": [99]}],
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_tooth"


async def test_zero_quantity_is_rejected(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Количество меньше единицы — это не позиция."""
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "scheduledAt": working_slot(),
            "items": [{"serviceId": ctx.filling_id, "quantity": 0}],
        },
    )
    assert resp.status_code == 422, resp.text


# ── 2. Правка чека ──────────────────────────────────────────────────────────


async def _replace(
    client: AsyncClient, ctx: ClinicCtx, appointment_id: str, items: list[dict[str, Any]]
) -> Any:
    return await client.put(
        f"{API}/clinic/appointments/{appointment_id}/items",
        headers=ctx.headers,
        json={"items": items},
    )


async def test_replace_items_recomputes_total(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Работы становятся известны по ходу приёма — чек дописывается и пересчитывается."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.cleaning_id}])

    resp = await _replace(
        client,
        ctx,
        appointment_id,
        [
            {"serviceId": ctx.cleaning_id},
            {"serviceId": ctx.filling_id, "teeth": [46], "quantity": 2},
        ],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["priceTotalTiyin"] == 7000
    assert len(resp.json()["items"]) == 2


async def test_replace_items_can_clear_the_bill(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Пустой список очищает чек: передаётся полное состояние, а не дифф."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [46]}])
    resp = await _replace(client, ctx, appointment_id, [])
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []
    assert resp.json()["priceTotalTiyin"] == 0


async def test_replace_items_keeps_price_at_the_time_of_service(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Цену позиции можно задать явно — прайс потом меняется, чек нет."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [46]}])
    resp = await _replace(
        client,
        ctx,
        appointment_id,
        [{"serviceId": ctx.filling_id, "teeth": [46], "priceTiyin": 2500}],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["priceTiyin"] == 2500
    assert resp.json()["priceTotalTiyin"] == 2500


async def test_replace_items_on_completed_is_409(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Завершённый приём — финансовый факт: чек меняют возвратом, а не правкой."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [46]}])
    done = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers
    )
    assert done.status_code == 200, done.text

    resp = await _replace(client, ctx, appointment_id, [{"serviceId": ctx.cleaning_id}])
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "appointment_completed"


async def test_replace_items_on_cancelled_is_422(client: AsyncClient, ctx: ClinicCtx) -> None:
    """У отменённого приёма чека нет — правке нечего менять."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [46]}])
    cancelled = await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=ctx.headers,
        json={"status": "cancelled", "cancelReason": "patient_request"},
    )
    assert cancelled.status_code == 200, cancelled.text

    resp = await _replace(client, ctx, appointment_id, [{"serviceId": ctx.cleaning_id}])
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "appointment_cancelled"


async def test_replace_items_below_paid_is_rejected(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Итог ниже оплаченного оставил бы приём с отрицательным долгом — 422.

    Предоплату принимают с `arrived` (решение M4), поэтому случай реальный:
    заплатили за большой план, а потом чек урезали.
    """
    appointment_id = await _book(
        client, ctx, [{"serviceId": ctx.filling_id, "teeth": [46], "quantity": 2}]
    )
    paid = await client.post(
        f"{API}/clinic/cash/payments",
        headers=ctx.headers,
        json={"appointmentId": appointment_id, "amountTiyin": 5000, "method": "cash"},
    )
    assert paid.status_code == 201, paid.text

    resp = await _replace(client, ctx, appointment_id, [{"serviceId": ctx.cleaning_id}])
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "total_below_paid"
    assert resp.json()["paidTiyin"] == 5000

    # Чек не тронут — откат сработал целиком.
    card = await _card(client, ctx, appointment_id)
    assert card["priceTotalTiyin"] == 6000
    assert len(card["items"]) == 1


async def test_replace_items_of_other_clinic_is_404(
    admin_client: AsyncClient, client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Чужой приём не правится — скоуп по клинике из токена."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [46]}])

    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    other = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Б",
            "adminPhone": phone,
            "name": "Клиника Б",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    other_headers = await _login(client, phone, other.json()["tempPassword"])

    resp = await client.put(
        f"{API}/clinic/appointments/{appointment_id}/items",
        headers=other_headers,
        json={"items": []},
    )
    assert resp.status_code == 404, resp.text


# ── 3. Статус позиции ───────────────────────────────────────────────────────


async def test_item_status_is_set_and_kept(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Статус позиции — то, что реально сделали внутри приёма, а не план целиком."""
    appointment_id = await _book(
        client,
        ctx,
        [
            {"serviceId": ctx.filling_id, "teeth": [36], "status": "started"},
            {"serviceId": ctx.cleaning_id, "status": "cancelled"},
        ],
    )
    card = await _card(client, ctx, appointment_id)
    statuses = {i["serviceId"]: i["status"] for i in card["items"]}
    assert statuses[ctx.filling_id] == "started"
    assert statuses[ctx.cleaning_id] == "cancelled"


async def test_default_status_is_planned(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Без явного статуса позиция — план: её ещё не делали."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [36]}])
    assert (await _card(client, ctx, appointment_id))["items"][0]["status"] == "planned"


async def test_completing_appointment_closes_open_items(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Закрыли приём — значит запланированное сделано; отменённое остаётся отменённым.

    Иначе статус так и висел бы вечным `planned`: отдельно проставлять его
    построчно на каждом закрытии никто не станет.
    """
    appointment_id = await _book(
        client,
        ctx,
        [
            {"serviceId": ctx.filling_id, "teeth": [36]},  # planned
            {"serviceId": ctx.filling_id, "teeth": [37], "status": "started"},
            {"serviceId": ctx.cleaning_id, "status": "cancelled"},
        ],
    )
    done = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers
    )
    assert done.status_code == 200, done.text

    statuses = sorted(i["status"] for i in done.json()["items"])
    assert statuses == ["cancelled", "completed", "completed"]


async def test_patch_to_completed_closes_items_too(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Закрытие через PATCH — тот же факт: позиции доводятся одинаково."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [36]}])
    resp = await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=ctx.headers,
        json={"status": "completed"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["status"] == "completed"


async def test_item_status_visible_in_tooth_history(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """История зуба показывает статус работы — там он и нужен."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [36]}])
    await client.post(f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers)

    hist = await client.get(
        f"{API}/clinic/patients/{ctx.patient_id}/teeth/36", headers=ctx.headers
    )
    assert hist.status_code == 200, hist.text
    assert hist.json()["records"][0]["status"] == "completed"


# ── 4. Отчёт услуг ──────────────────────────────────────────────────────────


async def test_services_report_counts_quantity(client: AsyncClient, ctx: ClinicCtx) -> None:
    """«Снимок ×3» — три оказанные услуги, а не одна строка отчёта."""
    # Отчёт за период: приём должен попасть в него, поэтому дата — сегодняшняя.
    await _book(client, ctx, [{"serviceId": ctx.cleaning_id, "quantity": 3}], when=today_slot())

    report = await client.get(f"{API}/clinic/reports/services", headers=ctx.headers)
    assert report.status_code == 200, report.text
    rows = {r["name"]: r for r in report.json()["rows"]}
    assert rows["Чистка"]["count"] == 3
    assert rows["Чистка"]["revenueTiyin"] == 3000
