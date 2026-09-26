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
from app.modules.payments.domain.periods import add_months
from app.modules.payments.presentation.deps import get_freedompay_client
from app.shared.db.models.subscription import Subscription
from app.shared.freedompay import (
    FreedomPayRejectedError,
    InitPaymentRequest,
    InitPaymentResult,
    sign,
)
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
        #: Провайдер не знает AUTOGET (нынешняя документация: только GET/POST).
        self.reject_auto = False
        #: Провайдер явно отказывает на любой запрос (например, неверная сумма).
        self.reject_all = False

    async def init_payment(self, request: InitPaymentRequest) -> InitPaymentResult:
        from app.shared.freedompay import FreedomPayError

        self.requests.append(request)
        if self.fail:
            raise FreedomPayError("101: Merchant not found")
        if self.reject_auto and request.success_url_method == "AUTOGET":
            raise FreedomPayRejectedError("340", "Invalid pg_success_url_method")
        if self.reject_all:
            raise FreedomPayRejectedError("200", "Invalid amount")
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
    monkeypatch.setattr(settings, "cabinet_url", "https://office.skrezo.com")
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
    async with _resources.factory() as session:
        trial_end = (
            await session.execute(
                select(Subscription.trial_ends_at).where(
                    Subscription.clinic_id == uuid.UUID(ctx.clinic_id)
                )
            )
        ).scalar_one()
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
    # Возврат — на страницу оплаты кабинета с id платежа, и при успехе, и при отказе;
    # returnMode не передан — кнопкой «Вернуться».
    back = f"https://office.skrezo.com/pay?payment={payment['id']}"
    assert sent.success_url == back and sent.failure_url == back and sent.user_phone
    assert sent.success_url_method == "GET" and sent.failure_url_method == "GET"
    assert sent.extra == {"pg_param1": ctx.clinic_id, "pg_param2": "subscription"}

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
    # Оплата на триале не сжигает оставшиеся дни: период — с конца триала (решение 26.09).
    assert sub.current_period_start == trial_end
    assert sub.current_period_end == add_months(trial_end, 2)
    assert sub.current_period_end - datetime.now(UTC) > timedelta(days=80)
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


async def test_return_mode_picks_return_method_and_autoget_rejection_falls_back(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    provider: _FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """returnMode: auto → AUTOGET, button → GET; отказ провайдера на AUTOGET не роняет оплату."""
    await _tariffs(admin_client)

    async def start(**body: Any) -> Any:
        resp = await client.post(
            f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json=body
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    auto = await start(months=1, returnMode="auto")
    assert provider.requests[-1].success_url_method == "AUTOGET"
    assert provider.requests[-1].failure_url_method == "AUTOGET"
    assert (
        provider.requests[-1].success_url == f"https://office.skrezo.com/pay?payment={auto['id']}"
    )
    await start(amountTiyin=300000, returnMode="button")
    assert provider.requests[-1].success_url_method == "GET"

    # Провайдер отверг AUTOGET — тот же заказ повторяется с кнопкой, платёж живой.
    provider.reject_auto = True
    before = len(provider.requests)
    fallback = await start(months=1, returnMode="auto")
    first, retry = provider.requests[before:]
    assert first.success_url_method == "AUTOGET" and retry.success_url_method == "GET"
    assert retry.failure_url_method == "GET" and retry.order_id == first.order_id == fallback["id"]
    assert fallback["status"] == "pending" and fallback["redirectUrl"]

    # Незнакомый режим — 422 валидации, до провайдера запрос не доходит.
    bad = await client.post(
        f"{API}/clinic/subscription/payments",
        headers=ctx.owner_headers,
        json={"months": 1, "returnMode": "popup"},
    )
    assert bad.status_code == 422

    # Кабинет не настроен — ни адреса возврата, ни метода: провайдер решает сам.
    monkeypatch.setattr(settings, "cabinet_url", "")
    await start(months=1, returnMode="auto")
    bare = provider.requests[-1]
    assert bare.success_url is None and bare.failure_url is None
    assert bare.success_url_method is None and bare.failure_url_method is None


async def test_init_is_retried_only_for_autoget_rejection(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    provider: _FakeProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повтор с тем же заказом — только после явного отказа на AUTOGET; выключатель AUTOGET."""
    await _tariffs(admin_client)

    async def start(**body: Any) -> Any:
        return await client.post(
            f"{API}/clinic/subscription/payments", headers=ctx.owner_headers, json=body
        )

    # Явный отказ при кнопке — не из-за способа возврата: повторять нечего.
    provider.reject_all = True
    before = len(provider.requests)
    rejected = await start(months=1, returnMode="button")
    assert rejected.status_code == 502 and rejected.json()["error"] == "payment_init_failed"
    assert len(provider.requests) == before + 1

    # Сеть или непонятный ответ при AUTOGET: платёж у провайдера мог родиться — без повтора.
    provider.reject_all, provider.fail = False, True
    before = len(provider.requests)
    failed = await start(months=1, returnMode="auto")
    assert failed.status_code == 502 and len(provider.requests) == before + 1

    # FREEDOMPAY_AUTO_RETURN=false — «auto» превращается в кнопку, AUTOGET не уходит вовсе.
    provider.fail = False
    monkeypatch.setattr(settings, "freedompay_auto_return", False)
    ok = await start(months=1, returnMode="auto")
    assert ok.status_code == 201, ok.text
    assert provider.requests[-1].success_url_method == "GET"
