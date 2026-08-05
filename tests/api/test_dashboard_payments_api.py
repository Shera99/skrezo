"""API-тесты дашборда (реальные метрики) и платежей (заглушки формы)."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import API


async def test_dashboard_summary_shape_and_counts(admin_client: AsyncClient) -> None:
    await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Метрика", "adminPhone": "0700700001"},
    )
    resp = await admin_client.get(f"{API}/admin/dashboard/summary?from=2026-07-01&to=2026-07-11")
    assert resp.status_code == 200
    body = resp.json()
    # Реальные метрики: один триал-соло → active/trial посчитаны.
    assert body["active"]["total"] == 1
    assert body["active"]["solo"] == 1
    assert body["trial"]["total"] == 1
    assert body["trial"]["noCard"] == 1
    # Биллинг-зависимые блоки — нули.
    assert body["mrr"]["value"] == 0
    assert body["risks"]["pastDue"] == 0
    assert "июля" in body["dateLabel"]


async def test_payments_stub(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(f"{API}/admin/payments?from=2026-07-01&to=2026-07-31")
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows"] == []
    assert body["totalCount"] == 0
    assert body["summary"]["netTiyin"] == 0

    refund = await admin_client.post(
        f"{API}/admin/payments/TXN-1/refund", json={"reason": "тест"}
    )
    assert refund.status_code == 404
    assert refund.json()["error"] == "payment_not_found"
