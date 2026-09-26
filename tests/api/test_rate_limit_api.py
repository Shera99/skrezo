"""API-тесты rate-limit (вход, выпуск OTP-кода и публичная лид-форма)."""

from __future__ import annotations

import pytest
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


async def test_otp_request_rate_limited(client: AsyncClient) -> None:
    """Выпуск кода на один номер ограничен: попытка сверх лимита в окне — 429."""
    statuses = []
    for _ in range(settings.otp_request_rate_limit + 1):
        resp = await client.post(f"{API}/auth/otp/request", json={"phone": "0700920001"})
        statuses.append(resp.status_code)
    assert statuses[: settings.otp_request_rate_limit] == [200] * settings.otp_request_rate_limit
    assert statuses[-1] == 429


@pytest.mark.parametrize("path", ["/auth/otp/request", "/auth/password/forgot"])
async def test_invalid_phone_does_not_spend_otp_limit(client: AsyncClient, path: str) -> None:
    """Некорректный номер — всегда 422 ``invalid_phone``, а не 429.

    SMS на такой номер не уходит, поэтому и попытку лимита он не тратит. Раньше
    тратил: пять опечаток подряд — и человек заперт на всё окно, не получив ни
    одного кода.
    """
    for _ in range(settings.otp_request_rate_limit + 1):
        resp = await client.post(f"{API}{path}", json={"phone": "string"})
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"] == "invalid_phone"
