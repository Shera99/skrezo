"""API-тесты единого входа и разграничения доступа по ролям.

Ключевой инвариант: паролем входит **только** команда Skrezo, все остальные — по
коду из SMS. Проверяется с обеих сторон, потому что дыра в любой из них сводит
разделение на нет.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import text

from app.core.security import hash_password
from tests.conftest import API, Resources, otp_code, otp_login


async def _provision_solo(admin_client: AsyncClient, phone: str) -> None:
    """Создать соло-клинику от имени админа (владелец — без пароля)."""
    resp = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Владелец Соло", "adminPhone": phone},
    )
    assert resp.status_code == 201, resp.text
    # Пароля в ответе нет вовсе — владельцу его больше не выдают.
    assert "tempPassword" not in resp.json()


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


async def test_owner_otp_login_scope_and_admin_forbidden(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Владелец входит по коду, получает скоуп своей клиники и не лезет в /admin."""
    await _provision_solo(admin_client, "0700555002")

    headers = await otp_login(client, "0700555002")

    forbidden = await client.get(f"{API}/admin/clients?page=1", headers=headers)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"] == "not_platform_admin"

    me = await client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["isPlatformAdmin"] is False
    assert me.json()["activeClinic"] is not None


async def test_clinic_account_with_password_still_cannot_login(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Пароль клиничному аккаунту не поможет, даже если хеш оказался в базе.

    Провижининг пароля не ставит, но хеш может остаться от старых данных или
    ручной правки. Проверка держит вторую сторону инварианта: обнуление хешей
    миграцией — не единственная защита, вход отказывает и сам по себе.
    """
    await _provision_solo(admin_client, "0700555003")
    async with _resources.factory() as session:
        await session.execute(
            text("UPDATE account SET password_hash = :hash WHERE phone = :phone"),
            {"hash": hash_password("secret123"), "phone": "+996700555003"},
        )
        await session.commit()

    resp = await client.post(
        f"{API}/auth/login", json={"phone": "0700555003", "password": "secret123"}
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"] == "invalid_credentials"

    # А по коду — заходит как обычно.
    assert await otp_login(client, "0700555003")


async def test_platform_admin_cannot_use_otp_login(
    client: AsyncClient, admin_token: str
) -> None:
    """Код из SMS не логинит админ-панель — иначе пароль админа был бы формальностью."""
    requested = await client.post(f"{API}/auth/otp/request", json={"phone": "0700000001"})
    assert requested.status_code == 200, requested.text

    verified = await client.post(
        f"{API}/auth/otp/verify",
        json={"phone": "0700000001", "code": await otp_code("0700000001")},
    )
    assert verified.status_code == 409, verified.text
    assert verified.json()["error"] == "use_password_login"
