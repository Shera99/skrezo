"""API-тесты self-service сброса пароля («забыл пароль»).

Пароль есть только у команды Skrezo, поэтому и восстановление — их путь: сброса
пароля владельцу клиники или работнику больше не существует, они входят по коду.

Ключевые риски:

* код чужого назначения не должен подходить ни в одну сторону — иначе
  «продиктуйте код для входа» превращается в угон аккаунта;
* аккаунту без пароля сбрасывать нечего (409), и узнаёт он это только после
  верного кода — перебирать чужие номера ответами нельзя;
* прежние сессии обязаны закрыться: пароль меняют в том числе после угона.
"""

from __future__ import annotations

import itertools

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.security import hash_password
from app.shared.db.enums import Lang
from app.shared.db.models import Account
from app.shared.domain.value_objects import normalize_phone
from tests.conftest import API, Resources, otp_code, otp_login

_phone_seq = itertools.count(1)
_ADMIN_PASSWORD = "staryy-parol-123"


def _phone() -> str:
    return f"070093{next(_phone_seq):04d}"


@pytest_asyncio.fixture
async def skrezo_admin(_resources: Resources) -> str:
    """Отдельный админ Skrezo с известным паролем; вернуть его телефон.

    Отдельный от фикстуры ``admin_token``: тесты здесь меняют пароль, и делать
    это аккаунту, которым авторизован ``admin_client``, значит ронять соседние
    запросы того же теста.
    """
    phone = _phone()
    async with _resources.factory() as session:
        session.add(
            Account(
                phone=normalize_phone(phone),
                full_name="Забывчивый Админ",
                lang=Lang.RU,
                onboarded=True,
                password_hash=hash_password(_ADMIN_PASSWORD),
                must_change_password=False,
                is_platform_admin=True,
            )
        )
        await session.commit()
    return phone


async def _login(client: AsyncClient, phone: str, password: str) -> int:
    """Попытка входа по паролю; вернуть статус-код."""
    resp = await client.post(f"{API}/auth/login", json={"phone": phone, "password": password})
    return resp.status_code


# ── Полный путь восстановления ──────────────────────────────────────────────


async def test_forgot_password_full_flow(client: AsyncClient, skrezo_admin: str) -> None:
    """Код → новый пароль: старый больше не подходит, смены пароля не требуют."""
    forgot = await client.post(f"{API}/auth/password/forgot", json={"phone": skrezo_admin})
    assert forgot.status_code == 200, forgot.text

    reset = await client.post(
        f"{API}/auth/password/reset",
        json={
            "phone": skrezo_admin,
            "code": await otp_code(skrezo_admin, "reset"),
            "newPassword": "novyy-parol-123",
        },
    )
    assert reset.status_code == 200, reset.text

    assert await _login(client, skrezo_admin, _ADMIN_PASSWORD) == 401

    new = await client.post(
        f"{API}/auth/login", json={"phone": skrezo_admin, "password": "novyy-parol-123"}
    )
    assert new.status_code == 200, new.text
    # Пароль выбрал сам владелец номера — временным он не считается.
    assert new.json()["mustChangePassword"] is False


async def test_reset_revokes_existing_sessions(client: AsyncClient, skrezo_admin: str) -> None:
    """Сессия, открытая до сброса, не переживает его: refresh → 401."""
    login = await client.post(
        f"{API}/auth/login", json={"phone": skrezo_admin, "password": _ADMIN_PASSWORD}
    )
    assert login.status_code == 200, login.text
    refresh_token = login.json()["refreshToken"]

    # До сброса токен рабочий.
    alive = await client.post(f"{API}/auth/refresh", json={"refreshToken": refresh_token})
    assert alive.status_code == 200, alive.text

    await client.post(f"{API}/auth/password/forgot", json={"phone": skrezo_admin})
    reset = await client.post(
        f"{API}/auth/password/reset",
        json={
            "phone": skrezo_admin,
            "code": await otp_code(skrezo_admin, "reset"),
            "newPassword": "drugoy-parol-123",
        },
    )
    assert reset.status_code == 200, reset.text

    dead = await client.post(f"{API}/auth/refresh", json={"refreshToken": refresh_token})
    assert dead.status_code == 401, dead.text


# ── Назначение кода ─────────────────────────────────────────────────────────


async def test_login_code_cannot_reset_password(client: AsyncClient, skrezo_admin: str) -> None:
    """Код, выпущенный на вход, пароль не меняет — иначе «продиктуйте код» = угон."""
    requested = await client.post(f"{API}/auth/otp/request", json={"phone": skrezo_admin})
    assert requested.status_code == 200, requested.text

    resp = await client.post(
        f"{API}/auth/password/reset",
        json={
            "phone": skrezo_admin,
            "code": await otp_code(skrezo_admin),
            "newPassword": "chuzhoy-kod-123",
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_otp"

    # Прежний пароль на месте.
    assert await _login(client, skrezo_admin, _ADMIN_PASSWORD) == 200


async def test_reset_code_cannot_log_in(client: AsyncClient, skrezo_admin: str) -> None:
    """И наоборот: кодом на смену пароля не войти."""
    await client.post(f"{API}/auth/password/forgot", json={"phone": skrezo_admin})

    resp = await client.post(
        f"{API}/auth/otp/verify",
        json={"phone": skrezo_admin, "code": await otp_code(skrezo_admin, "reset")},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_otp"


async def test_reset_with_wrong_code_is_400(client: AsyncClient, skrezo_admin: str) -> None:
    """Неверный код → 400, пароль не тронут."""
    await client.post(f"{API}/auth/password/forgot", json={"phone": skrezo_admin})

    resp = await client.post(
        f"{API}/auth/password/reset",
        json={"phone": skrezo_admin, "code": "000000", "newPassword": "ne-proydet-123"},
    )
    assert resp.status_code == 400, resp.text

    assert await _login(client, skrezo_admin, _ADMIN_PASSWORD) == 200


# ── Кому сбрасывать нечего ──────────────────────────────────────────────────


async def test_forgot_for_unknown_phone_does_not_reveal_it(client: AsyncClient) -> None:
    """Незнакомый номер отвечает как знакомый; 404 приходит только после кода."""
    phone = _phone()
    forgot = await client.post(f"{API}/auth/password/forgot", json={"phone": phone})
    assert forgot.status_code == 200, forgot.text
    assert forgot.json()["sent"] is True

    resp = await client.post(
        f"{API}/auth/password/reset",
        json={
            "phone": phone,
            "code": await otp_code(phone, "reset"),
            "newPassword": "nekomu-menyat-123",
        },
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "account_not_found"


@pytest.mark.parametrize("clinic_type", ["solo", "clinic"])
async def test_clinic_account_has_no_password_to_reset(
    client: AsyncClient, admin_client: AsyncClient, clinic_type: str
) -> None:
    """Клиника и соло входят по коду — сбрасывать им нечего (409 use_otp_login)."""
    phone = _phone()
    payload: dict[str, object] = {
        "type": clinic_type,
        "adminFullName": "Владелец Без Пароля",
        "adminPhone": phone,
    }
    if clinic_type == "clinic":
        city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
        payload |= {
            "name": f"Клиника {phone}",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        }
    created = await admin_client.post(f"{API}/admin/clients", json=payload)
    assert created.status_code == 201, created.text

    await client.post(f"{API}/auth/password/forgot", json={"phone": phone})
    resp = await client.post(
        f"{API}/auth/password/reset",
        json={
            "phone": phone,
            "code": await otp_code(phone, "reset"),
            "newPassword": "chuzhoy-parol-123",
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "use_otp_login"

    # Вход по коду при этом работает.
    assert await otp_login(client, phone)


async def test_change_password_rejects_otp_account(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    """И смена своего пароля тоже: у клиничного аккаунта его нет (409)."""
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Соло Без Пароля", "adminPhone": phone},
    )
    assert created.status_code == 201, created.text

    headers = await otp_login(client, phone)
    resp = await client.post(
        f"{API}/auth/change-password",
        headers=headers,
        json={"currentPassword": "cokolibo", "newPassword": "novyy-parol-123"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "use_otp_login"
