"""API-тесты тарифов (модель «пакеты + overPrice», версии, валидация)."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import API

_PACKAGES = [
    {"name": "Старт", "upTo": 3, "priceTiyin": 3000},
    {"name": "Про", "upTo": 6, "priceTiyin": 5000},
    {"name": "Макс", "upTo": 10, "priceTiyin": 8000},
]


async def test_update_solo_and_bundle(admin_client: AsyncClient) -> None:
    created = await admin_client.post(f"{API}/admin/tariffs/solo", json={"priceTiyin": 3000})
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1

    bundle = await admin_client.get(f"{API}/admin/tariffs")
    assert bundle.status_code == 200
    assert bundle.json()["solo"]["priceTiyin"] == 3000
    assert bundle.json()["solo"]["version"] == 1


async def test_update_clinic_packages(admin_client: AsyncClient) -> None:
    created = await admin_client.post(
        f"{API}/admin/tariffs/clinic", json={"packages": _PACKAGES, "overPriceTiyin": 800}
    )
    assert created.status_code == 201, created.text
    assert created.json()["version"] == 1
    assert "affectedClients" in created.json()

    bundle = await admin_client.get(f"{API}/admin/tariffs")
    clinic = bundle.json()["clinic"]
    assert clinic["overPriceTiyin"] == 800
    assert [p["upTo"] for p in clinic["packages"]] == [3, 6, 10]
    # История содержит текущую версию с summary.
    current = [h for h in bundle.json()["history"] if h["current"] and h["kind"] == "clinic"]
    assert current and "11+: 800/место" in current[0]["summary"]


async def test_clinic_validation_422(admin_client: AsyncClient) -> None:
    bad = await admin_client.post(
        f"{API}/admin/tariffs/clinic",
        json={
            "packages": [
                {"name": "A", "upTo": 3, "priceTiyin": 3000},
                {"name": "B", "upTo": 3, "priceTiyin": 5000},  # up_to не возрастает
            ],
            "overPriceTiyin": 800,
        },
    )
    assert bad.status_code == 422
    assert bad.json()["error"] == "tariff_not_increasing"


async def test_version_increments(admin_client: AsyncClient) -> None:
    v1 = await admin_client.post(f"{API}/admin/tariffs/solo", json={"priceTiyin": 3000})
    v2 = await admin_client.post(f"{API}/admin/tariffs/solo", json={"priceTiyin": 3500})
    assert v1.json()["version"] == 1
    assert v2.json()["version"] == 2
    bundle = await admin_client.get(f"{API}/admin/tariffs")
    assert bundle.json()["solo"]["priceTiyin"] == 3500  # текущая = максимальная версия
