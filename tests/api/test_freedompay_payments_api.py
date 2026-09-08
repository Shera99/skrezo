"""Оплата подписки через платёжную страницу FreedomPay.

Владелец видит цену по тарифу, начинает платёж и получает ссылку; период
открывает только подписанный серверный колбэк; повтор колбэка, чужая
подпись, несовпавшая сумма и отказ — каждый со своим ответом провайдеру;
Skrezo видит журнал и отмечает возврат.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.main import app
from app.modules.payments.presentation.deps import get_freedompay_client
from app.shared.db.models.subscription import Subscription
from app.shared.freedompay import InitPaymentRequest, InitPaymentResult, sign
from tests.api.conftest import Ctx
from tests.conftest import API, Resources

PACKAGES = [
    {"name": "Старт", "upTo": 3, "priceTiyin": 300000},
    {"name": "Практика", "upTo": 6, "priceTiyin": 500000},
]


class _FakeProvider:
    def __init__(self) -> None:
        self.requests: list[InitPaymentRequest] = []
        self.fail = False

    async def init_payment(self, request: InitPaymentRequest) -> InitPaymentResult:
        from app.shared.freedompay import FreedomPayError

        self.requests.append(request)
        if self.fail:
            raise FreedomPayError("101: Merchant not found")
        return InitPaymentResult(
            payment_id=f"fp-{len(self.requests)}",
            redirect_url=f"https://pay.test/{request.order_id}",
        )


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Any:
    fake = _FakeProvider()
    monkeypatch.setattr(settings, "freedompay_secret_key", "SECRET")
    monkeypatch.setattr(settings, "freedompay_merchant_id", "12345")
    monkeypatch.setattr(settings, "public_api_url", "https://api.test/api/v1")
    monkeypatch.setattr(settings, "freedompay_success_url", "https://app.test/billing/ok")
    app.dependency_overrides[get_freedompay_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_freedompay_client, None)


async def _tariffs(admin_client: AsyncClient) -> None:
    resp = await admin_client.post(
        f"{API}/admin/tariffs/clinic", json={"packages": PACKAGES, "overPriceTiyin": 80000}
    )
    assert resp.status_code == 201, resp.text


def _callback(
    payment_id: str,
    provider_id: str,
    amount: str,
    *,
    result: str = "1",
    secret: str = "SECRET",
    **extra: str,
) -> dict[str, str]:
    params = {
        "pg_order_id": payment_id,
        "pg_payment_id": provider_id,
        "pg_result": result,
        "pg_amount": amount,
        "pg_currency": "KGS",
        "pg_salt": "cb-salt",
        "pg_payment_date": "2026-09-08 10:00:00",
        "pg_can_reject": "1",
        **extra,
    }
    params["pg_sig"] = sign("result", params, secret)
    return params


async def _post_result(client: AsyncClient, params: dict[str, str]) -> str:
    resp = await client.post(f"{API}/payments/freedompay/result", data=params)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/xml")
    return resp.text


async def test_quote_start_and_signed_callback_opens_period(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    provider: _FakeProvider,
    _resources: Resources,
) -> None:
    await _tariffs(admin_client)
    quote = await client.get(
        f"{API}/clinic/subscription/price", headers=ctx.owner_headers, params={"months": 2}
    )
    assert quote.status_code == 200, quote.text
    assert quote.json() == {
        "monthlyTiyin": 500000,  # 5 мест → пакет «Практика»
        "months": 2,
        "totalTiyin": 1000000,
        "seats": 5,
        "currency": "KGS",
        "available": True,
    }

    started = await client.post(
        f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json={"months": 2}
    )
    assert started.status_code == 201, started.text
    payment = started.json()
    assert payment["status"] == "pending" and payment["amountTiyin"] == 1000000
    assert payment["redirectUrl"] == f"https://pay.test/{payment['id']}"
    sent = provider.requests[0]
    assert sent.amount == "10000.00" and sent.currency == "KGS" and sent.testing_mode is True
    assert sent.result_url == "https://api.test/api/v1/payments/freedompay/result"
    assert sent.success_url == "https://app.test/billing/ok" and sent.user_phone
    assert sent.extra == {"pg_param1": ctx.clinic_id}

    # Колбэк с верной подписью и суммой → оплачено, период на 2 месяца открыт.
    xml = await _post_result(client, _callback(payment["id"], "fp-1", "10000.00"))
    assert "<pg_status>ok</pg_status>" in xml and "<pg_sig>" in xml
    state = await client.get(
        f"{API}/clinic/subscription/payments/{payment['id']}", headers=ctx.owner_headers
    )
    assert state.json()["status"] == "paid" and state.json()["redirectUrl"] is None
    assert state.json()["periodEnd"] is not None
    async with _resources.factory() as session:
        sub = (
            await session.execute(
                select(Subscription).where(Subscription.clinic_id == uuid.UUID(ctx.clinic_id))
            )
        ).scalar_one()
    assert sub.status.value == "active" and sub.current_period_end is not None
    assert timedelta(days=58) < sub.current_period_end - datetime.now(UTC) < timedelta(days=63)
    subscription = await client.get(f"{API}/clinic/subscription", headers=ctx.owner_headers)
    assert subscription.json()["status"] == "active"

    # Повтор колбэка — ok, второго продления нет.
    again = await _post_result(client, _callback(payment["id"], "fp-1", "10000.00"))
    assert "<pg_status>ok</pg_status>" in again
    async with _resources.factory() as session:
        sub2 = (
            await session.execute(
                select(Subscription).where(Subscription.clinic_id == uuid.UUID(ctx.clinic_id))
            )
        ).scalar_one()
    assert sub2.current_period_end == sub.current_period_end

    # Второй платёж продлевает от конца текущего периода, а не от сегодня.
    second = await client.post(
        f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json={"months": 1}
    )
    await _post_result(client, _callback(second.json()["id"], "fp-2", "5000.00"))
    async with _resources.factory() as session:
        sub3 = (
            await session.execute(
                select(Subscription).where(Subscription.clinic_id == uuid.UUID(ctx.clinic_id))
            )
        ).scalar_one()
    assert sub3.current_period_end is not None and sub.current_period_end is not None
    assert (
        timedelta(days=27) < sub3.current_period_end - sub.current_period_end < timedelta(days=32)
    )

    listing = await client.get(f"{API}/clinic/subscription/payments", headers=ctx.owner_headers)
    assert listing.json()["totalCount"] == 2 and listing.json()["list"][0]["status"] == "paid"


async def test_callback_guards_signature_amount_and_failure(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, provider: _FakeProvider
) -> None:
    await _tariffs(admin_client)

    async def start() -> str:
        resp = await client.post(
            f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json={"months": 1}
        )
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    async def status_of(payment_id: str) -> str:
        resp = await client.get(
            f"{API}/clinic/subscription/payments/{payment_id}", headers=ctx.owner_headers
        )
        return str(resp.json()["status"])

    forged = await start()
    xml = await _post_result(client, _callback(forged, "fp-1", "5000.00", secret="WRONG"))
    assert "<pg_status>error</pg_status>" in xml and await status_of(forged) == "pending"

    short = await start()
    xml = await _post_result(client, _callback(short, "fp-2", "4999.00"))
    assert "<pg_status>rejected</pg_status>" in xml and await status_of(short) == "failed"

    declined = await start()
    xml = await _post_result(
        client,
        _callback(
            declined,
            "fp-3",
            "5000.00",
            result="0",
            pg_failure_code="100",
            pg_failure_description="Declined",
        ),
    )
    assert "<pg_status>ok</pg_status>" in xml
    resp = await client.get(
        f"{API}/clinic/subscription/payments/{declined}", headers=ctx.owner_headers
    )
    assert resp.json()["status"] == "failed" and resp.json()["failureCode"] == "100"

    unknown = await _post_result(client, _callback(str(uuid.uuid4()), "fp-9", "5000.00"))
    assert "<pg_status>error</pg_status>" in unknown

    subscription = await client.get(f"{API}/clinic/subscription", headers=ctx.owner_headers)
    assert subscription.json()["status"] == "trial"  # ничего из этого период не открыло


async def test_provider_not_configured_and_admin_journal_with_refund(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, provider: _FakeProvider
) -> None:
    await _tariffs(admin_client)
    provider.fail = True
    failed = await client.post(
        f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json={"months": 1}
    )
    assert failed.status_code == 502 and failed.json()["error"] == "payment_init_failed"
    provider.fail = False

    started = await client.post(
        f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json={"months": 1}
    )
    payment_id = started.json()["id"]
    await _post_result(client, _callback(payment_id, "fp-2", "5000.00"))

    journal = await admin_client.get(f"{API}/admin/payments", params={"status": "success"})
    assert journal.status_code == 200, journal.text
    rows = journal.json()["rows"]
    assert [r["txnId"] for r in rows] == ["fp-2"] and rows[0]["status"] == "success"
    assert rows[0]["amountTiyin"] == 500000 and rows[0]["method"] == "FreedomPay"
    assert journal.json()["summary"]["successSumTiyin"] == 500000

    refund = await admin_client.post(
        f"{API}/admin/payments/{payment_id}/refund", json={"reason": "по просьбе клиники"}
    )
    assert refund.status_code == 200, refund.text
    again = await admin_client.post(
        f"{API}/admin/payments/{payment_id}/refund", json={"reason": "x"}
    )
    assert again.status_code == 409 and again.json()["error"] == "payment_not_paid"
    journal = await admin_client.get(f"{API}/admin/payments")
    summary = journal.json()["summary"]
    assert summary["refundCount"] == 1 and summary["refundSumTiyin"] == -500000
    assert summary["netTiyin"] == 0
    refunded = next(r for r in journal.json()["rows"] if r["id"] == payment_id)
    assert refunded["type"] == "Возврат" and refunded["amountTiyin"] == -500000

    # Реквизитов нет — цена видна, оплата недоступна.
    app.dependency_overrides[get_freedompay_client] = lambda: None
    quote = await client.get(f"{API}/clinic/subscription/price", headers=ctx.owner_headers)
    assert quote.json()["available"] is False
    blocked = await client.post(
        f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json={"months": 1}
    )
    assert blocked.status_code == 409 and blocked.json()["error"] == "payments_not_configured"
