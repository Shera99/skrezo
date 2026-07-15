"""API-тесты rate-limit (вход и публичная лид-форма)."""

from __future__ import annotations

from httpx import AsyncClient

from app.core.config import settings
from tests.conftest import API


async def test_login_rate_limited(client: AsyncClient) -> None:
    statuses = []
    for _ in range(settings.login_rate_limit + 1):
        resp = await client.post(
            f"{API}/auth/login", json={"phone": "0700900001", "password": "x"}
        )
        statuses.append(resp.status_code)
    # Первые N попыток — обычный 401, попытка сверх лимита — 429.
    assert statuses[: settings.login_rate_limit] == [401] * settings.login_rate_limit
    assert statuses[-1] == 429


async def test_lead_rate_limited(client: AsyncClient) -> None:
    statuses = []
    for i in range(settings.lead_rate_limit + 1):
        resp = await client.post(
            f"{API}/leads",
            json={
                "fullName": "Заявка",
                "phone": f"070091000{i}",
                "clinicName": "Клиника",
            },
        )
        statuses.append(resp.status_code)
    assert statuses[: settings.lead_rate_limit] == [201] * settings.lead_rate_limit
    assert statuses[-1] == 429
    assert "retryAfter" in (
        await client.post(
            f"{API}/leads",
            json={"fullName": "X", "phone": "0700919999", "clinicName": "K"},
        )
    ).json()
