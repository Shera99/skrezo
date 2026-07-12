"""API-тесты единого входа и разграничения доступа по ролям."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import API


async def _provision_solo(admin_client: AsyncClient, phone: str) -> str:
    """Создать соло-клинику от имени админа; вернуть временный пароль владельца."""
    resp = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Владелец Соло", "adminPhone": phone},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tempPassword"])


async def test_no_token_is_401(client: AsyncClient) -> None:
    resp = await client.get(f"{API}/admin/clients?page=1")
    assert resp.status_code == 401


async def test_admin_login_and_access(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(f"{API}/admin/clients?page=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["currentPage"] == 1
    assert body["pageSize"] == 25


async def test_admin_me_flags_platform_admin(admin_client: AsyncClient) -> None:
    resp = await admin_client.get(f"{API}/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["isPlatformAdmin"] is True
    assert body["activeClinic"] is None


async def test_wrong_password_is_401(client: AsyncClient, admin_token: str) -> None:
    resp = await client.post(
        f"{API}/auth/login", json={"phone": "0700000001", "password": "WRONG"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_credentials"


async def test_owner_login_scope_and_admin_forbidden(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    temp_password = await _provision_solo(admin_client, "0700555002")

    # Владелец входит тем же /auth/login: платформенным админом НЕ является.
    login = await client.post(
        f"{API}/auth/login", json={"phone": "0700555002", "password": temp_password}
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["isPlatformAdmin"] is False
    assert body["mustChangePassword"] is True  # временный пароль
    assert len(body["clinics"]) == 1
    owner_token = body["accessToken"]

    headers = {"Authorization": f"Bearer {owner_token}"}
    forbidden = await client.get(f"{API}/admin/clients?page=1", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"] == "not_platform_admin"

    me = await client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["isPlatformAdmin"] is False
    assert me.json()["activeClinic"] is not None
