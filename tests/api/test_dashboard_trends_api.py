"""API-тесты трендов дашборда (ряды по месяцам)."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import API


async def test_trends_shape_and_current_month(admin_client: AsyncClient) -> None:
    # Один соло-клиент и одна заявка — попадают в текущий (последний) месяц.
    await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Тренд", "adminPhone": "0700800001"},
    )
    lead = await admin_client.post(
        f"{API}/leads",
        json={"fullName": "Заявка", "phone": "0700800002", "clinicName": "Клиника"},
    )
    assert lead.status_code == 201, lead.text

    resp = await admin_client.get(f"{API}/admin/dashboard/trends?months=6")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["mrr"]) == 6
    assert len(body["activeClients"]) == 6
    assert len(body["leadsToClients"]) == 6
    assert all(p["value"] == 0 for p in body["mrr"])  # MRR — заглушка (биллинг)

    # Текущий месяц — последний бакет.
    assert body["leadsToClients"][-1]["clients"] == 1
    assert body["leadsToClients"][-1]["leads"] == 1
    assert body["activeClients"][-1] == {
        "month": body["activeClients"][-1]["month"],
        "solo": 1,
        "clinic": 0,
    }
    assert body["soloClinicRatio"] == {"solo": 1, "clinic": 0}


async def test_trends_months_param(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(f"{API}/admin/dashboard/trends?months=12")
    assert resp.status_code == 200
    assert len(resp.json()["mrr"]) == 12
    # Пустая база — везде нули и пустой срез.
    assert resp.json()["soloClinicRatio"] == {"solo": 0, "clinic": 0}
    assert all(p["clients"] == 0 for p in resp.json()["leadsToClients"])
