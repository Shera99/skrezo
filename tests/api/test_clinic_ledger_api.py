"""Журнал операций и баланс клиники у Skrezo.

Оплата подписки провайдером = приход + списание в одной операции; пополнение
остаётся на балансе и продлевает подписку, когда она ждёт денег; списание с
баланса руками; ручные операции Skrezo; возврат уводит остаток в минус;
автопродление ночью — с баланса или предупреждение; остаток сходится с
журналом.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

import app.jobs.tasks.billing as billing_tasks
from app.core.config import settings
from app.main import app
from app.modules.payments.presentation.deps import get_freedompay_client
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.clinic_ledger_entry import ClinicLedgerEntry
from app.shared.db.models.subscription import Subscription
from app.shared.freedompay import InitPaymentRequest, InitPaymentResult, sign
from tests.api.conftest import Ctx
from tests.conftest import API, Resources

PACKAGES = [
    {"name": "Старт", "upTo": 3, "priceTiyin": 300000},
    {"name": "Практика", "upTo": 6, "priceTiyin": 500000},
]
MONTHLY = 500000  # 5 мест → «Практика»


class _FakeProvider:
    def __init__(self) -> None:
        self.requests: list[InitPaymentRequest] = []

    async def init_payment(self, request: InitPaymentRequest) -> InitPaymentResult:
        self.requests.append(request)
        return InitPaymentResult(
            payment_id=f"fp-{len(self.requests)}",
            redirect_url=f"https://pay.test/{request.order_id}",
            raw={"pg_status": "ok", "pg_payment_id": f"fp-{len(self.requests)}"},
        )


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake = _FakeProvider()
    monkeypatch.setattr(settings, "freedompay_secret_key", "SECRET")
    monkeypatch.setattr(settings, "freedompay_merchant_id", "12345")
    monkeypatch.setattr(settings, "public_api_url", "https://api.test/api/v1")
    app.dependency_overrides[get_freedompay_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_freedompay_client, None)


async def _tariffs(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        f"{API}/admin/tariffs/clinic", json={"packages": PACKAGES, "overPriceTiyin": 80000}
    )
    assert resp.status_code == 201, resp.text


def _callback(payment_id: str, provider_id: str, amount: str) -> dict[str, str]:
    params = {
        "pg_order_id": payment_id,
        "pg_payment_id": provider_id,
        "pg_result": "1",
        "pg_amount": amount,
        "pg_currency": "KGS",
        "pg_salt": "cb-salt",
        "pg_can_reject": "1",
    }
    params["pg_sig"] = sign("result", params, "SECRET")
    return params


async def _pay(client: AsyncClient, ctx: Ctx, **body: Any) -> Any:
    started = await client.post(
        f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json=body
    )
    assert started.status_code == 201, started.text
    return started.json()


async def _ledger(client: AsyncClient, ctx: Ctx) -> Any:
    resp = await client.get(f"{API}/clinic/ledger", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _subscription(client: AsyncClient, ctx: Ctx) -> Any:
    resp = await client.get(f"{API}/clinic/subscription", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _assert_ledger_consistent(res: Resources, clinic_id: str, expected_balance: int) -> None:
    async with res.factory() as session:
        rows = list(
            (
                await session.execute(
                    select(ClinicLedgerEntry)
                    .where(ClinicLedgerEntry.clinic_id == uuid.UUID(clinic_id))
                    .order_by(ClinicLedgerEntry.created_at, ClinicLedgerEntry.id)
                )
            ).scalars()
        )
    balance = 0
    for row in rows:
        balance += row.amount_tiyin if row.kind == "credit" else -row.amount_tiyin
        assert row.balance_after_tiyin == balance, "остаток после операции не сходится с журналом"
    assert balance == expected_balance


async def test_subscription_payment_is_credit_plus_charge_and_topup_stays_on_balance(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    provider: _FakeProvider,
    _resources: Resources,
) -> None:
    await _tariffs(admin_client)
    assert (await _ledger(client, ctx)) == {
        "balanceTiyin": 0,
        "autopayEnabled": True,
        "list": [],
        "currentPage": 1,
        "pageSize": 25,
        "totalCount": 0,
    }

    # Оплата подписки: приход и сразу списание — баланс 0, период открыт.
    paid = await _pay(client, ctx, months=2)
    assert paid["purpose"] == "subscription"
    resp = await client.post(
        f"{API}/payments/freedompay/result", data=_callback(paid["id"], "fp-1", "10000.00")
    )
    assert "<pg_status>ok</pg_status>" in resp.text
    ledger = await _ledger(client, ctx)
    assert ledger["balanceTiyin"] == 0 and ledger["totalCount"] == 2
    charge, credit = ledger["list"]
    assert (
        credit["kind"] == "credit"
        and credit["source"] == "freedompay_payment"
        and credit["amountTiyin"] == 1000000
    )
    assert (
        charge["kind"] == "debit"
        and charge["source"] == "subscription_charge"
        and charge["balanceAfterTiyin"] == 0
    )
    assert charge["periodEnd"] is not None and charge["paymentId"] == paid["id"]
    assert (await _subscription(client, ctx))["status"] == "active"

    # Повтор колбэка — приход не дублируется.
    await client.post(
        f"{API}/payments/freedompay/result", data=_callback(paid["id"], "fp-1", "10000.00")
    )
    assert (await _ledger(client, ctx))["totalCount"] == 2

    # Пополнение: приход остаётся на балансе (подписка в силе и до конца далеко).
    topup = await _pay(client, ctx, amountTiyin=1500000)
    assert topup["purpose"] == "topup" and provider.requests[-1].extra["pg_param2"] == "topup"
    await client.post(
        f"{API}/payments/freedompay/result", data=_callback(topup["id"], "fp-2", "15000.00")
    )
    ledger = await _ledger(client, ctx)
    assert ledger["balanceTiyin"] == 1500000 and ledger["list"][0]["source"] == "freedompay_payment"
    sub = await _subscription(client, ctx)
    assert sub["balanceTiyin"] == 1500000 and sub["autopayEnabled"] is True

    # Оплата с баланса: списание за 2 месяца от конца текущего периода.
    before_end = sub["currentPeriodEnd"]
    charged = await client.post(
        f"{API}/clinic/subscription/pay-from-balance", headers=ctx.owner_headers, json={"months": 2}
    )
    assert charged.status_code == 201, charged.text
    assert (
        charged.json()["amountTiyin"] == 2 * MONTHLY
        and charged.json()["balanceAfterTiyin"] == 500000
    )
    after = await _subscription(client, ctx)
    assert after["balanceTiyin"] == 500000 and after["currentPeriodEnd"] > before_end

    # Не хватает — 402 с недостачей, журнал не меняется.
    short = await client.post(
        f"{API}/clinic/subscription/pay-from-balance", headers=ctx.owner_headers, json={"months": 2}
    )
    assert short.status_code == 402 and short.json()["error"] == "insufficient_balance"
    assert short.json()["shortfallTiyin"] == 500000 and short.json()["balanceTiyin"] == 500000
    assert (await _ledger(client, ctx))["totalCount"] == 4
    await _assert_ledger_consistent(_resources, ctx.clinic_id, 500000)

    # Минимум пополнения и пустой запрос.
    tiny = await client.post(
        f"{API}/clinic/subscription/payments",
        headers=ctx.owner_headers,
        json={"amountTiyin": 50000},
    )
    assert tiny.status_code == 422 and tiny.json()["error"] == "topup_too_small"
    empty = await client.post(
        f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json={}
    )
    assert empty.status_code == 422


async def test_manual_topup_renews_waiting_subscription_and_refund_goes_negative(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    provider: _FakeProvider,
    _resources: Resources,
) -> None:
    await _tariffs(admin_client)
    # Триал истёк — подписка ждёт денег.
    async with _resources.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(ctx.clinic_id))
            .values(status=SubscriptionStatus.TRIAL_EXPIRED)
        )
        await session.commit()

    entry = await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={"kind": "credit", "amountTiyin": 1200000, "comment": "Перевод по счёту №7"},
    )
    assert entry.status_code == 201, entry.text
    assert entry.json()["source"] == "manual_topup" and entry.json()["balanceAfterTiyin"] == 1200000
    # Перевод на счёт сразу продлил на месяц: 1 200 000 − 500 000.
    sub = await _subscription(client, ctx)
    assert sub["status"] == "active" and sub["balanceTiyin"] == 700000
    admin_ledger = await admin_client.get(f"{API}/admin/clients/{ctx.clinic_id}/ledger")
    assert admin_ledger.json()["balanceTiyin"] == 700000
    assert [e["source"] for e in admin_ledger.json()["list"]] == [
        "subscription_charge",
        "manual_topup",
    ]

    adjust = await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={
            "kind": "debit",
            "amountTiyin": 200000,
            "comment": "Корректировка: ошибочно зачислено",
        },
    )
    assert adjust.status_code == 201 and adjust.json()["source"] == "adjustment"

    # Оплата провайдером и возврат: расход уводит остаток в минус — это факт.
    paid = await _pay(client, ctx, months=1)
    await client.post(
        f"{API}/payments/freedompay/result", data=_callback(paid["id"], "fp-1", "5000.00")
    )
    assert (await _ledger(client, ctx))["balanceTiyin"] == 500000
    refund = await admin_client.post(
        f"{API}/admin/payments/{paid['id']}/refund", json={"reason": "по просьбе клиники"}
    )
    assert refund.status_code == 200, refund.text
    ledger = await _ledger(client, ctx)
    assert ledger["balanceTiyin"] == 0 and ledger["list"][0]["source"] == "refund"
    await _assert_ledger_consistent(_resources, ctx.clinic_id, 0)


async def test_autopay_renews_from_balance_or_warns_once(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _tariffs(admin_client)
    warned: list[tuple[str, str]] = []

    class _Notifier:
        async def notify_phone(self, phone: str, text: str, *, event: str) -> None:
            warned.append((phone, text))

        async def notify_team(self, text: str, *, event: str) -> None:
            warned.append(("team", text))

    monkeypatch.setattr(billing_tasks, "session_factory", _resources.factory)
    monkeypatch.setattr(billing_tasks, "notifier", lambda ctx: _Notifier())
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory", _resources.factory
    )
    from fakeredis import aioredis

    redis = aioredis.FakeRedis()

    # Активная подписка, период кончается через 2 дня, баланса нет → предупреждение один раз.
    soon = datetime.now(UTC) + timedelta(days=2)
    async with _resources.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(ctx.clinic_id))
            .values(
                status=SubscriptionStatus.ACTIVE,
                current_period_start=datetime.now(UTC) - timedelta(days=28),
                current_period_end=soon,
            )
        )
        await session.commit()
    await _ledger(client, ctx)  # кошелёк заводится при первом обращении
    assert "предупреждено: 1" in await billing_tasks.autopay_subscriptions({"redis": redis})
    assert any("не хватает 5000 сом" in text for _, text in warned)
    assert "предупреждено: 0" in await billing_tasks.autopay_subscriptions(
        {"redis": redis}
    )  # повторно не трогаем

    # Положили деньги переводом — продлилось сразу, ночью уже нечего продлевать.
    topup = await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={"kind": "credit", "amountTiyin": 600000, "comment": "Перевод"},
    )
    assert topup.status_code == 201
    sub = await _subscription(client, ctx)
    assert sub["balanceTiyin"] == 100000 and sub["currentPeriodEnd"] > soon.isoformat()
    assert "продлено: 0" in await billing_tasks.autopay_subscriptions({"redis": redis})

    # Автопродление выключено — ночью ничего не списывается даже при деньгах.
    off = await client.patch(
        f"{API}/clinic/subscription/autopay", headers=ctx.owner_headers, json={"enabled": False}
    )
    assert off.status_code == 200 and off.json()["autopayEnabled"] is False
    async with _resources.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(ctx.clinic_id))
            .values(current_period_end=soon)
        )
        await session.commit()
    await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={"kind": "credit", "amountTiyin": 900000, "comment": "Перевод"},
    )
    assert "продлено: 0, предупреждено: 0" in await billing_tasks.autopay_subscriptions(
        {"redis": redis}
    )
    assert (await _subscription(client, ctx))["balanceTiyin"] == 1000000

    # Включили обратно — ночное задание списало месяц с баланса.
    on = await client.patch(
        f"{API}/clinic/subscription/autopay", headers=ctx.owner_headers, json={"enabled": True}
    )
    assert on.status_code == 200
    assert "продлено: 1" in await billing_tasks.autopay_subscriptions({"redis": redis})
    assert (await _subscription(client, ctx))["balanceTiyin"] == 500000
    await _assert_ledger_consistent(_resources, ctx.clinic_id, 500000)
