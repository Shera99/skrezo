"""Журнал операций и баланс клиники у Skrezo.

Оплата подписки провайдером = приход + списание в одной операции; пополнение
остаётся на балансе и продлевает подписку, когда она ждёт денег; списание с
баланса руками; ручные операции Skrezo; возврат уводит остаток в минус;
автопродление ночью — с баланса или предупреждение; остаток сходится с
журналом.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

import app.jobs.tasks.billing as billing_tasks
import app.jobs.tasks.subscriptions as subscription_tasks
from app.core.config import settings
from app.jobs.worker import WorkerSettings
from app.main import app
from app.modules.access.application.services.subscription_policy import SubscriptionPolicy
from app.modules.access.infrastructure.repositories.subscription_policy_repository import (
    SubscriptionPolicyRepository,
)
from app.modules.payments.application.services.ledger_service import LedgerService
from app.modules.payments.domain.ledger import KIND_CREDIT, SOURCE_TOPUP
from app.modules.payments.domain.periods import add_months
from app.modules.payments.presentation.deps import get_freedompay_client
from app.modules.subscriptions.application.use_cases.manage_subscription import (
    ManageSubscriptionUseCase,
)
from app.modules.subscriptions.infrastructure.repositories.subscription_repository import (
    SubscriptionRepository,
)
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.clinic_ledger_entry import ClinicLedgerEntry
from app.shared.db.models.clinic_wallet import ClinicWallet
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


# ── Триал: оплата не сжигает оставшиеся дни (решение владельца 26.09.2026) ──────


async def _patch_billing(
    monkeypatch: pytest.MonkeyPatch, res: Resources, warned: list[tuple[str, str]]
) -> Any:
    """Ночное задание автопродления на тестовой базе; сообщения копятся в ``warned``."""

    class _Notifier:
        async def notify_phone(self, phone: str, text: str, *, event: str) -> None:
            warned.append((phone, text))

        async def notify_team(self, text: str, *, event: str) -> None:
            warned.append(("team", text))

    # Оба ночных задания — продление и предупреждения — пишут в один список:
    # так видно, что клиника получила за ночь в порядке расписания.
    for tasks in (billing_tasks, subscription_tasks):
        monkeypatch.setattr(tasks, "session_factory", res.factory)
        monkeypatch.setattr(tasks, "notifier", lambda ctx: _Notifier())
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory", res.factory
    )
    from fakeredis import aioredis

    return aioredis.FakeRedis()


async def _db_subscription(res: Resources, clinic_id: str) -> Subscription:
    async with res.factory() as session:
        return (
            await session.execute(
                select(Subscription).where(Subscription.clinic_id == uuid.UUID(clinic_id))
            )
        ).scalar_one()


async def _set_trial_end(res: Resources, clinic_id: str, trial_end: datetime) -> None:
    async with res.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(clinic_id))
            .values(status=SubscriptionStatus.TRIAL, trial_ends_at=trial_end)
        )
        await session.commit()


async def test_topup_on_trial_waits_and_autopay_opens_period_at_trial_end(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _tariffs(admin_client)
    warned: list[tuple[str, str]] = []
    redis = await _patch_billing(monkeypatch, _resources, warned)

    # Пополнение на триале остаётся на балансе: до конца триала далеко.
    topup = await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={"kind": "credit", "amountTiyin": 1200000, "comment": "Перевод"},
    )
    assert topup.status_code == 201, topup.text
    sub = await _subscription(client, ctx)
    assert sub["status"] == "trial" and sub["balanceTiyin"] == 1200000
    assert "продлено: 0, предупреждено: 0" in await billing_tasks.autopay_subscriptions(
        {"redis": redis}
    )

    # До конца триала два дня — ночное задание списывает месяц, период с конца триала.
    trial_end = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    await _set_trial_end(_resources, ctx.clinic_id, trial_end)
    assert "продлено: 1" in await billing_tasks.autopay_subscriptions({"redis": redis})
    renewed = await _db_subscription(_resources, ctx.clinic_id)
    assert renewed.status is SubscriptionStatus.ACTIVE
    assert renewed.current_period_start == trial_end
    assert renewed.current_period_end == add_months(trial_end, 1)
    assert (await _subscription(client, ctx))["balanceTiyin"] == 1200000 - MONTHLY

    # Дальше подписка в силе и до конца далеко — второй раз не списывает.
    assert "продлено: 0" in await billing_tasks.autopay_subscriptions({"redis": redis})
    assert warned == []
    await _assert_ledger_consistent(_resources, ctx.clinic_id, 1200000 - MONTHLY)


async def test_pay_from_balance_on_trial_keeps_remaining_days(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    await _tariffs(admin_client)
    trial_end = (await _db_subscription(_resources, ctx.clinic_id)).trial_ends_at
    await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={"kind": "credit", "amountTiyin": 600000, "comment": "Перевод"},
    )
    charged = await client.post(
        f"{API}/clinic/subscription/pay-from-balance", headers=ctx.owner_headers, json={"months": 1}
    )
    assert charged.status_code == 201, charged.text
    assert charged.json()["periodStart"] is not None
    sub = await _db_subscription(_resources, ctx.clinic_id)
    assert sub.status is SubscriptionStatus.ACTIVE
    assert sub.current_period_start == trial_end
    assert sub.current_period_end == add_months(trial_end, 1)
    assert (await _subscription(client, ctx))["balanceTiyin"] == 600000 - MONTHLY


async def test_autopay_leaves_trial_without_money_alone(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Триал без денег в последние дни: ни списания, ни письма о балансе, ни кошелька."""
    await _tariffs(admin_client)
    warned: list[tuple[str, str]] = []
    redis = await _patch_billing(monkeypatch, _resources, warned)
    await _set_trial_end(_resources, ctx.clinic_id, datetime.now(UTC) + timedelta(days=1))

    assert "продлено: 0, предупреждено: 0" in await billing_tasks.autopay_subscriptions(
        {"redis": redis}
    )
    assert warned == []  # о конце триала предупреждает своё задание
    async with _resources.factory() as session:
        wallet = (
            await session.execute(
                select(ClinicWallet).where(ClinicWallet.clinic_id == uuid.UUID(ctx.clinic_id))
            )
        ).scalar_one_or_none()
    assert wallet is None
    assert (await _db_subscription(_resources, ctx.clinic_id)).status is SubscriptionStatus.TRIAL


async def test_concurrent_renewals_charge_one_period_once(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Колбэк и ночное задание продлевают одновременно — месяц списывается один раз.

    Две сессии, как два процесса: обе видят триал в последние дни и деньги на
    два месяца. Под блокировкой кошелька вторая видит уже открытый период и
    молчит; до исправления обе списывали за один и тот же месяц, а остаток
    считали от одной цифры.
    """
    await _tariffs(admin_client)
    warned: list[tuple[str, str]] = []
    redis = await _patch_billing(monkeypatch, _resources, warned)
    await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={"kind": "credit", "amountTiyin": 2 * MONTHLY, "comment": "Перевод"},
    )
    trial_end = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    await _set_trial_end(_resources, ctx.clinic_id, trial_end)
    clinic_id = uuid.UUID(ctx.clinic_id)

    # Принудительное вклинивание: прочитав подписку для списания, сессия ждёт,
    # пока её прочитает и вторая (не дольше секунды). Если чтение идёт ДО
    # блокировки кошелька, обе видят триал и списывают дважды; при верном
    # порядке вторая стоит на блокировке, первая дожидается таймаута и
    # коммитит, а вторая читает уже открытый период.
    original = SubscriptionRepository.by_clinic
    readers: list[bool] = []
    both_read = asyncio.Event()

    async def by_clinic_in_step(
        self: SubscriptionRepository, clinic: uuid.UUID, *, fresh: bool = False
    ) -> Subscription | None:
        found = await original(self, clinic, fresh=fresh)
        if fresh:
            readers.append(True)
            if len(readers) >= 2:
                both_read.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(both_read.wait(), timeout=1.0)
        return found

    monkeypatch.setattr(SubscriptionRepository, "by_clinic", by_clinic_in_step)

    async def renew() -> bool:
        async with _resources.factory() as session:
            manage = ManageSubscriptionUseCase(
                session,
                SubscriptionRepository(session),
                SubscriptionPolicy(SubscriptionPolicyRepository(session), redis),
            )
            entry = await LedgerService(session).try_autorenew(
                clinic_id, manage=manage, reason="autopay_balance"
            )
            return entry is not None

    results = await asyncio.gather(renew(), renew())
    assert sorted(results) == [False, True]
    renewed = await _db_subscription(_resources, ctx.clinic_id)
    assert renewed.current_period_start == trial_end
    assert renewed.current_period_end == add_months(trial_end, 1)
    ledger = await _ledger(client, ctx)
    assert [e["source"] for e in ledger["list"]] == ["subscription_charge", "manual_topup"]
    await _assert_ledger_consistent(_resources, ctx.clinic_id, MONTHLY)


async def test_trial_short_of_money_is_neither_charged_nor_warned(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Триал в последние дни, на балансе меньше месяца: ни списания, ни письма о балансе."""
    await _tariffs(admin_client)
    warned: list[tuple[str, str]] = []
    redis = await _patch_billing(monkeypatch, _resources, warned)
    await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={"kind": "credit", "amountTiyin": MONTHLY // 2, "comment": "Перевод"},
    )
    await _set_trial_end(_resources, ctx.clinic_id, datetime.now(UTC) + timedelta(days=1))
    assert "продлено: 0, предупреждено: 0" in await billing_tasks.autopay_subscriptions(
        {"redis": redis}
    )
    assert warned == []  # о конце триала предупреждает warn_trial_ending
    sub = await _subscription(client, ctx)
    assert sub["status"] == "trial" and sub["balanceTiyin"] == MONTHLY // 2


def test_autopay_runs_before_expiry_warnings() -> None:
    """Порядок в расписании: продление с баланса — раньше предупреждений со ссылкой."""
    at = {
        job.name.removeprefix("cron:"): (job.hour, job.minute) for job in WorkerSettings.cron_jobs
    }
    assert at["autopay_subscriptions"] < at["warn_trial_ending"]
    assert at["autopay_subscriptions"] < at["warn_period_ending"]


async def test_renewed_trial_gets_no_pay_now_warning(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ночью в порядке расписания: продлили с баланса — «оплатите сейчас» не уходит."""
    await _tariffs(admin_client)
    warned: list[tuple[str, str]] = []
    redis = await _patch_billing(monkeypatch, _resources, warned)
    await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={"kind": "credit", "amountTiyin": MONTHLY, "comment": "Перевод"},
    )
    await _set_trial_end(_resources, ctx.clinic_id, datetime.now(UTC) + timedelta(days=2))

    assert "продлено: 1" in await billing_tasks.autopay_subscriptions({"redis": redis})
    assert await subscription_tasks.warn_trial_ending({"redis": redis}) == 0
    assert warned == []


async def test_locked_wallet_read_sees_commit_of_another_session(
    ctx: Ctx, _resources: Resources
) -> None:
    """Блокировка кошелька перечитывает остаток: иначе списание считало бы от старой цифры.

    Ночное задание загружает кошельки в начале прогона; если за это время
    другой процесс провёл деньги, решение под блокировкой обязано видеть их.
    """
    clinic_id = uuid.UUID(ctx.clinic_id)
    async with _resources.factory() as night:
        ledger = LedgerService(night)
        # Ссылка держится, как в ночном задании весь прогон: иначе сессия (она
        # хранит объекты по слабым ссылкам) выбросила бы снимок сама.
        snapshot = await ledger.wallet(clinic_id)
        assert snapshot.balance_tiyin == 0
        async with _resources.factory() as other:
            await LedgerService(other).post(
                clinic_id,
                kind=KIND_CREDIT,
                source=SOURCE_TOPUP,
                amount_tiyin=300000,
                description="Перевод",
            )
            await other.commit()
        locked = await ledger.wallet(clinic_id, lock=True)
        assert locked is snapshot and locked.balance_tiyin == 300000
        await night.rollback()


# ── Ручной блок и уход клиента деньгами не снимаются ────────────────────────────


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (SubscriptionStatus.BLOCKED, "subscription_blocked"),
        (SubscriptionStatus.CANCELLED, "subscription_cancelled"),
    ],
)
async def test_money_does_not_reopen_subscription_closed_by_skrezo(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
    status: SubscriptionStatus,
    code: str,
) -> None:
    await _tariffs(admin_client)
    warned: list[tuple[str, str]] = []
    redis = await _patch_billing(monkeypatch, _resources, warned)
    async with _resources.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(ctx.clinic_id))
            .values(status=status)
        )
        await session.commit()

    # Перевод на счёт при включённом автопродлении — деньги на балансе, статус тот же.
    topup = await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/ledger/entries",
        json={"kind": "credit", "amountTiyin": 2 * MONTHLY, "comment": "Перевод"},
    )
    assert topup.status_code == 201, topup.text
    assert (await _db_subscription(_resources, ctx.clinic_id)).status is status

    # Оплата с баланса — 409 с понятным кодом, журнал не меняется.
    charged = await client.post(
        f"{API}/clinic/subscription/pay-from-balance", headers=ctx.owner_headers, json={"months": 1}
    )
    assert charged.status_code == 409 and charged.json()["error"] == code
    # Ночное задание тоже не трогает.
    assert "продлено: 0" in await billing_tasks.autopay_subscriptions({"redis": redis})
    ledger = await _ledger(client, ctx)
    assert ledger["balanceTiyin"] == 2 * MONTHLY and ledger["totalCount"] == 1
    assert (await _db_subscription(_resources, ctx.clinic_id)).status is status


async def test_blocked_clinic_can_top_up_but_not_buy_and_late_callback_keeps_money(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    provider: _FakeProvider,
    _resources: Resources,
) -> None:
    await _tariffs(admin_client)
    # Платёж начат, пока подписка открыта, — владелец ушёл на страницу оплаты.
    pending = await _pay(client, ctx, months=1)
    blocked = await admin_client.post(
        f"{API}/admin/clients/{ctx.clinic_id}/subscription/block",
        json={"reason": "нарушение условий"},
    )
    assert blocked.status_code == 200, blocked.text

    # Новая оплата подписки — 409 ещё до провайдера; пополнение — можно.
    before = len(provider.requests)
    refused = await client.post(
        f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json={"months": 1}
    )
    assert refused.status_code == 409 and refused.json()["error"] == "subscription_blocked"
    assert len(provider.requests) == before
    topup = await _pay(client, ctx, amountTiyin=300000)

    # Колбэк за платёж, начатый до блока: деньги пришли — на баланс, период не открыт.
    late = await client.post(
        f"{API}/payments/freedompay/result",
        data=_callback(pending["id"], "fp-1", "5000.00"),
    )
    assert "<pg_status>ok</pg_status>" in late.text
    await client.post(
        f"{API}/payments/freedompay/result", data=_callback(topup["id"], "fp-2", "3000.00")
    )
    sub = await _db_subscription(_resources, ctx.clinic_id)
    assert sub.status is SubscriptionStatus.BLOCKED and sub.current_period_end is None
    ledger = await _ledger(client, ctx)
    assert ledger["balanceTiyin"] == MONTHLY + 300000
    assert {e["source"] for e in ledger["list"]} == {"freedompay_payment"}
    await _assert_ledger_consistent(_resources, ctx.clinic_id, MONTHLY + 300000)


async def test_expired_period_short_of_money_is_warned_once_not_every_night(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Просрочка без денег: «не хватает на балансе» — один раз, а не каждую ночь."""
    await _tariffs(admin_client)
    warned: list[tuple[str, str]] = []
    redis = await _patch_billing(monkeypatch, _resources, warned)
    ended = datetime.now(UTC) - timedelta(days=1)
    async with _resources.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(ctx.clinic_id))
            .values(
                status=SubscriptionStatus.PAST_DUE,
                current_period_start=ended - timedelta(days=30),
                current_period_end=ended,
            )
        )
        await session.commit()
    await _ledger(client, ctx)  # кошелёк заводится при первом обращении

    nights = [await billing_tasks.autopay_subscriptions({"redis": redis}) for _ in range(3)]
    assert ["предупреждено: 1" in n for n in nights] == [True, False, False]
    assert len(warned) == 2  # владельцу и в чат команды — один раз


async def test_clinic_sees_its_tariff_packages(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx
) -> None:
    """Кабинет видит пакеты своего тарифа и текущий по числу мест — без админки."""
    missing = await client.get(f"{API}/clinic/subscription/packages", headers=ctx.owner_headers)
    assert missing.status_code == 409 and missing.json()["error"] == "tariff_not_configured"

    await _tariffs(admin_client)
    resp = await client.get(f"{API}/clinic/subscription/packages", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    card = resp.json()
    assert card["type"] == "clinic" and card["seats"] == 5
    assert [(p["name"], p["upTo"], p["priceTiyin"], p["current"]) for p in card["packages"]] == [
        ("Старт", 3, 300000, False),
        ("Практика", 6, 500000, True),
    ]
    assert card["overPriceTiyin"] == 80000 and card["currentPackage"] == "Практика"
    # Та же цена, что и у оплаты.
    price = await client.get(f"{API}/clinic/subscription/price", headers=ctx.owner_headers)
    assert card["monthlyTiyin"] == price.json()["monthlyTiyin"] == MONTHLY
