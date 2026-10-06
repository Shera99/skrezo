"""Смена тарифа клиникой и спецплан от Skrezo.

Тариф клиники — публичные пакеты по числу мест; между ними клиника переходит
сама, сверх них — заявка и спецплан команды Skrezo. Проверяется то, на чём
легко ошибиться деньгами и доступом:

* дороже — доплата за остаток оплаченного периода с баланса, не хватает — 402
  и ничего не меняется; двойной клик не списывает дважды;
* дешевле — бесплатно, но только если сотрудники помещаются; лимит мест
  после перехода действительно держит добавление сотрудников;
* на триале переход без доплаты;
* спецплан заменяет цену пакетов везде, закрывает переход и заявку, а после
  снятия цена снова по пакетам;
* закрытая Skrezo подписка и чужие права перейти не дают.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.main import app
from app.shared.db.models.clinic_ledger_entry import ClinicLedgerEntry
from app.shared.notifications.deps import get_notifier
from tests.api.conftest import Ctx
from tests.conftest import API, Resources, otp_login

PACKAGES = [
    {"name": "Старт", "upTo": 3, "priceTiyin": 300000},
    {"name": "Практика", "upTo": 6, "priceTiyin": 500000},
    {"name": "Клиника", "upTo": 10, "priceTiyin": 800000},
]
OVER_PRICE = 80000


class _Team:
    """Подменённый отправитель: копит сообщения команде Skrezo."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def notify_team(self, text: str, *, event: str) -> None:
        self.messages.append(text)

    async def notify_phone(self, phone: str, text: str, *, event: str) -> None:
        return None


@pytest_asyncio.fixture
async def team() -> Any:
    fake = _Team()
    app.dependency_overrides[get_notifier] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_notifier, None)


async def _tariffs(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        f"{API}/admin/tariffs/clinic", json={"packages": PACKAGES, "overPriceTiyin": OVER_PRICE}
    )
    assert resp.status_code == 201, resp.text


async def _activate(admin_client: AsyncClient, ctx: Ctx, *, days: int) -> None:
    paid_until = (datetime.now(UTC) + timedelta(days=days)).isoformat()
    resp = await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/subscription/activate",
        json={"paidUntil": paid_until},
    )
    assert resp.status_code == 200, resp.text


async def _topup(admin_client: AsyncClient, ctx: Ctx, amount: int) -> None:
    resp = await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={"kind": "credit", "amountTiyin": amount, "comment": "Перевод по счёту"},
    )
    assert resp.status_code == 201, resp.text


async def _plan(client: AsyncClient, ctx: Ctx) -> Any:
    resp = await client.get(f"{API}/clinic/subscription/packages", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _change(client: AsyncClient, ctx: Ctx, up_to: int) -> Any:
    return await client.put(
        f"{API}/clinic/subscription/package", headers=ctx.owner_headers, json={"upTo": up_to}
    )


async def _add_staff(client: AsyncClient, ctx: Ctx) -> Any:
    return await client.post(
        f"{API}/clinic/staff",
        headers=ctx.owner_headers,
        json={"fullName": "Сотрудник Клиники", "phone": f"0555{str(uuid.uuid4().int)[:6]}"},
    )


async def _plan_changes(res: Resources, clinic_id: str) -> list[ClinicLedgerEntry]:
    async with res.factory() as session:
        return list(
            (
                await session.execute(
                    select(ClinicLedgerEntry).where(
                        ClinicLedgerEntry.clinic_id == uuid.UUID(clinic_id),
                        ClinicLedgerEntry.source == "plan_change",
                    )
                )
            ).scalars()
        )


def _by_name(plan: Any) -> dict[str, Any]:
    return {p["name"]: p for p in plan["packages"]}


# ── 1. Витрина: куда можно перейти и сколько стоит ─────────────────────────


async def test_packages_show_what_clinic_can_switch_to_and_the_cost_now(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx
) -> None:
    await _tariffs(admin_client)
    await _activate(admin_client, ctx, days=20)

    plan = await _plan(client, ctx)
    assert plan["seatsLimit"] == 5 and plan["seatsUsed"] == 0
    assert plan["currentPackage"] == "Практика" and plan["monthlyTiyin"] == 500000
    assert plan["canChange"] is True and plan["changeBlockedReason"] is None
    packages = _by_name(plan)
    # Дешевле — без доплаты; текущий с лимитом ниже границы — открыть места бесплатно.
    assert packages["Старт"]["available"] and packages["Старт"]["changeCostTiyin"] == 0
    assert packages["Практика"]["current"] and packages["Практика"]["available"]
    assert packages["Практика"]["changeCostTiyin"] == 0
    # Дороже — разница 3 000 сом за 20 дней из 30: 2 000 сом.
    assert packages["Клиника"]["changeCostTiyin"] == 200000


# ── 2. Дороже: доплата с баланса ───────────────────────────────────────────


async def test_upgrade_charges_remaining_period_from_balance(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    team: _Team,
    _resources: Resources,
) -> None:
    await _tariffs(admin_client)
    await _activate(admin_client, ctx, days=20)
    await _topup(admin_client, ctx, 150000)

    short = await _change(client, ctx, 10)
    assert short.status_code == 402, short.text
    body = short.json()
    assert body["error"] == "insufficient_balance"
    assert (body["amountTiyin"], body["shortfallTiyin"]) == (200000, 50000)
    assert (await _plan(client, ctx))["seatsLimit"] == 5  # ничего не изменилось

    await _topup(admin_client, ctx, 100000)
    done = await _change(client, ctx, 10)
    assert done.status_code == 200, done.text
    result = done.json()
    assert result["fromPackage"] == "Практика" and result["toPackage"] == "Клиника"
    assert result["chargedTiyin"] == 200000 and result["chargedDays"] == 20
    assert result["balanceTiyin"] == 50000 and result["seatsLimit"] == 10
    assert result["monthlyTiyin"] == 800000
    assert result["plan"]["currentPackage"] == "Клиника"
    assert _by_name(result["plan"])["Клиника"]["unavailableReason"] == "current"

    [entry] = await _plan_changes(_resources, ctx.clinic_id)
    assert entry.kind == "debit" and entry.amount_tiyin == 200000
    assert any("Практика → Клиника" in m and "2 000" in m for m in team.messages)

    # Новая цена — во всём, что считает подписку.
    price = await client.get(
        f"{API}/clinic/subscription/price", headers=ctx.owner_headers, params={"months": 1}
    )
    assert price.json()["monthlyTiyin"] == 800000

    again = await _change(client, ctx, 10)
    assert again.status_code == 409 and again.json()["error"] == "package_already_current"


async def test_double_click_charges_once(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    await _tariffs(admin_client)
    await _activate(admin_client, ctx, days=20)
    await _topup(admin_client, ctx, 1000000)

    first, second = await asyncio.gather(_change(client, ctx, 10), _change(client, ctx, 10))
    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409], (first.text, second.text)
    assert len(await _plan_changes(_resources, ctx.clinic_id)) == 1


# ── 3. Дешевле и триал ─────────────────────────────────────────────────────


async def test_downgrade_is_free_but_staff_must_fit_and_limit_holds(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    await _tariffs(admin_client)
    for _ in range(4):
        assert (await _add_staff(client, ctx)).status_code == 201

    blocked = await _change(client, ctx, 3)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"] == "seats_in_use"
    assert (blocked.json()["seatsUsed"], blocked.json()["upTo"]) == (4, 3)
    assert _by_name(await _plan(client, ctx))["Старт"]["unavailableReason"] == "seats_in_use"

    # На триале переход дороже — без доплаты.
    up = await _change(client, ctx, 10)
    assert up.status_code == 200, up.text
    assert up.json()["chargedTiyin"] == 0 and up.json()["seatsLimit"] == 10
    assert await _plan_changes(_resources, ctx.clinic_id) == []

    down = await _change(client, ctx, 6)
    assert down.status_code == 200 and down.json()["chargedTiyin"] == 0
    assert (await _add_staff(client, ctx)).status_code == 201
    assert (await _add_staff(client, ctx)).status_code == 201
    full = await _add_staff(client, ctx)
    assert full.status_code == 409 and full.json()["error"] == "seats_limit_reached"


# ── 4. Заявка и спецплан ───────────────────────────────────────────────────


async def test_request_then_custom_plan_replaces_packages_until_removed(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, team: _Team
) -> None:
    await _tariffs(admin_client)

    first = await client.post(
        f"{API}/clinic/subscription/plan-request",
        headers=ctx.owner_headers,
        json={"seats": 14, "comment": "Открываем второй филиал"},
    )
    assert first.status_code == 200, first.text
    again = await client.post(
        f"{API}/clinic/subscription/plan-request",
        headers=ctx.owner_headers,
        json={"seats": 15},
    )
    assert again.json()["id"] == first.json()["id"] and again.json()["seats"] == 15
    assert (await _plan(client, ctx))["openRequest"]["seats"] == 15
    assert any("15" in m and "Клиника ИИ" in m for m in team.messages)

    queue = await admin_client.get(f"{API}/admin/plan-requests", params={"status": "new"})
    mine = [r for r in queue.json()["list"] if r["clinicId"] == ctx.clinic_id]
    assert len(mine) == 1 and mine[0]["clinicName"] == "Клиника ИИ"

    plan = await admin_client.put(
        f"{API}/admin/clients/{ctx.clinic_id}/custom-plan",
        json={"title": "Клиника+ 15 мест", "seats": 15, "priceTiyin": 1100000, "note": "на год"},
    )
    assert plan.status_code == 200, plan.text
    card = (await admin_client.get(f"{API}/admin/clients/{ctx.clinic_id}")).json()
    assert card["customPlan"]["title"] == "Клиника+ 15 мест" and card["seatsLimit"] == 15
    assert card["openPlanRequest"] is None  # заявка закрылась спецпланом
    closed = await admin_client.get(f"{API}/admin/plan-requests", params={"status": "done"})
    assert any(r["id"] == first.json()["id"] for r in closed.json()["list"])

    view = await _plan(client, ctx)
    assert view["customPlan"] == {
        "title": "Клиника+ 15 мест",
        "seats": 15,
        "monthlyTiyin": 1100000,
    }
    assert view["monthlyTiyin"] == 1100000 and view["seatsLimit"] == 15
    assert view["canChange"] is False and view["changeBlockedReason"] == "custom_plan_active"
    assert all(not p["available"] for p in view["packages"])
    assert view["openRequest"] is None
    refused = await _change(client, ctx, 10)
    assert refused.status_code == 409 and refused.json()["error"] == "custom_plan_active"
    price = await client.get(
        f"{API}/clinic/subscription/price", headers=ctx.owner_headers, params={"months": 2}
    )
    assert price.json()["totalTiyin"] == 2200000

    removed = await admin_client.delete(f"{API}/admin/clients/{ctx.clinic_id}/custom-plan")
    assert removed.status_code == 204, removed.text
    back = await _plan(client, ctx)
    # Лимит прежний — 15 мест: «Клиника» и пять мест сверх неё.
    assert back["customPlan"] is None and back["seatsLimit"] == 15
    assert back["monthlyTiyin"] == 800000 + 5 * OVER_PRICE and back["canChange"] is True
    missing = await admin_client.delete(f"{API}/admin/clients/{ctx.clinic_id}/custom-plan")
    assert missing.status_code == 404 and missing.json()["error"] == "custom_plan_not_found"


async def test_custom_plan_needs_room_for_staff_and_a_price(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx
) -> None:
    await _tariffs(admin_client)
    for _ in range(3):
        assert (await _add_staff(client, ctx)).status_code == 201
    small = await admin_client.put(
        f"{API}/admin/clients/{ctx.clinic_id}/custom-plan",
        json={"title": "Мини", "seats": 2, "priceTiyin": 100000},
    )
    assert small.status_code == 409 and small.json()["seatsUsed"] == 3
    free = await admin_client.put(
        f"{API}/admin/clients/{ctx.clinic_id}/custom-plan",
        json={"title": "Даром", "seats": 5, "priceTiyin": 0},
    )
    assert free.status_code == 422


async def test_plan_request_is_resolved_once(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx
) -> None:
    created = await client.post(
        f"{API}/clinic/subscription/plan-request", headers=ctx.owner_headers, json={"seats": 20}
    )
    request_id = created.json()["id"]
    declined = await admin_client.patch(
        f"{API}/admin/plan-requests/{request_id}",
        json={"status": "declined", "note": "Пока не готовы"},
    )
    assert declined.status_code == 200, declined.text
    assert declined.json()["status"] == "declined" and declined.json()["clinicName"]
    twice = await admin_client.patch(
        f"{API}/admin/plan-requests/{request_id}", json={"status": "done"}
    )
    assert twice.status_code == 409 and twice.json()["error"] == "plan_request_closed"
    unknown = await admin_client.patch(
        f"{API}/admin/plan-requests/{uuid.uuid4()}", json={"status": "done"}
    )
    assert unknown.status_code == 404


# ── 5. Кто и когда не может ────────────────────────────────────────────────


async def test_blocked_subscription_and_foreign_roles_cannot_change(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx
) -> None:
    await _tariffs(admin_client)
    phone = f"0555{str(uuid.uuid4().int)[:6]}"
    added = await client.post(
        f"{API}/clinic/staff",
        headers=ctx.owner_headers,
        json={"fullName": "Врач Без Прав", "phone": phone},
    )
    assert added.status_code == 201, added.text
    worker = await otp_login(client, phone)
    denied = await client.put(
        f"{API}/clinic/subscription/package", headers=worker, json={"upTo": 10}
    )
    assert denied.status_code == 403, denied.text
    owner_as_admin = await client.put(
        f"{API}/admin/clients/{ctx.clinic_id}/custom-plan",
        headers=ctx.owner_headers,
        json={"title": "Сам себе", "seats": 50, "priceTiyin": 1},
    )
    assert owner_as_admin.status_code in (401, 403), owner_as_admin.text

    blocked = await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/subscription/block", json={"reason": "тест"}
    )
    assert blocked.status_code == 200, blocked.text
    view = await _plan(client, ctx)
    assert view["canChange"] is False and view["changeBlockedReason"] == "subscription_blocked"
    refused = await _change(client, ctx, 10)
    assert refused.status_code == 409 and refused.json()["error"] == "subscription_blocked"
