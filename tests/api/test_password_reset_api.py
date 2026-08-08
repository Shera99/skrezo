"""API-тесты сброса пароля.

Три пути, и они не взаимозаменяемы:

* админ-сайд — Skrezo сбрасывает пароль владельцу клиента;
* клиент-сайд — владелец/менеджер сбрасывает пароль работнику;
* self-service «забыл пароль» — по коду из сообщения, без чьей-либо помощи
  (нужен, когда владелец сам заперт снаружи).

Риски self-service: код чужого назначения не должен подходить, соло-врачу
сбрасывать нечего, а прежние сессии обязаны закрыться — пароль меняют в том
числе после угона.
"""

from __future__ import annotations

import itertools

from httpx import AsyncClient

from app.shared.domain.value_objects import normalize_phone
from tests.conftest import API, Resources

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070093{next(_phone_seq):04d}"


async def _reset_code(res: Resources, phone: str) -> str:
    """Код назначения ``reset`` из fakeredis (доставка заглушена)."""
    code: str | None = await res.fake.get(f"auth:otp:code:reset:{normalize_phone(phone)}")
    assert code is not None, "код на смену пароля не выпущен"
    return code


async def _create_clinic_owner(admin_client: AsyncClient, phone: str) -> str:
    """Завести клинику с владельцем на этом номере; вернуть временный пароль."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Забывчивый Владелец",
            "adminPhone": phone,
            "name": f"Клиника {phone}",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    temp: str = created.json()["tempPassword"]
    return temp


async def test_admin_reset_admin_password(client: AsyncClient, admin_client: AsyncClient) -> None:
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Сброс", "adminPhone": "0700910001"},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    temp1 = created.json()["tempPassword"]

    # Старый временный пароль сейчас работает.
    ok1 = await client.post(
        f"{API}/auth/login", json={"phone": "0700910001", "password": temp1}
    )
    assert ok1.status_code == 200

    # Админ сбрасывает → новый временный пароль.
    reset = await admin_client.post(f"{API}/admin/clients/{cid}/reset-password")
    assert reset.status_code == 200, reset.text
    temp2 = reset.json()["tempPassword"]
    assert temp2 != temp1

    # Старый больше не подходит, новый — подходит и требует смены.
    old = await client.post(f"{API}/auth/login", json={"phone": "0700910001", "password": temp1})
    assert old.status_code == 401
    new = await client.post(f"{API}/auth/login", json={"phone": "0700910001", "password": temp2})
    assert new.status_code == 200
    assert new.json()["mustChangePassword"] is True


async def test_owner_resets_worker_password(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]

    clinic = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": "0700920001",
            "name": "Клиника",
            "cityId": city,
            "businessPhone": "0700920000",
            "seats": 5,
        },
    )
    cid = clinic.json()["id"]
    owner_temp = clinic.json()["tempPassword"]

    doctor = await admin_client.post(
        f"{API}/admin/clients/{cid}/doctors",
        json={"fullName": "Врач", "phone": "0700920002", "specializationId": spec},
    )
    doc_id = doctor.json()["id"]

    owner_login = await client.post(
        f"{API}/auth/login", json={"phone": "0700920001", "password": owner_temp}
    )
    owner_token = owner_login.json()["accessToken"]

    reset = await client.post(
        f"{API}/clinic/staff/{doc_id}/reset-password",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert reset.status_code == 200, reset.text
    worker_temp = reset.json()["tempPassword"]

    worker_login = await client.post(
        f"{API}/auth/login", json={"phone": "0700920002", "password": worker_temp}
    )
    assert worker_login.status_code == 200
    assert worker_login.json()["mustChangePassword"] is True


# ── Self-service «забыл пароль» ─────────────────────────────────────────────


async def test_forgot_password_full_flow(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Код → новый пароль: старый больше не подходит, смены пароля не требуют."""
    phone = _phone()
    old_password = await _create_clinic_owner(admin_client, phone)

    forgot = await client.post(f"{API}/auth/password/forgot", json={"phone": phone})
    assert forgot.status_code == 200, forgot.text

    reset = await client.post(
        f"{API}/auth/password/reset",
        json={
            "phone": phone,
            "code": await _reset_code(_resources, phone),
            "newPassword": "novyy-parol-123",
        },
    )
    assert reset.status_code == 200, reset.text

    old = await client.post(f"{API}/auth/login", json={"phone": phone, "password": old_password})
    assert old.status_code == 401, old.text

    new = await client.post(
        f"{API}/auth/login", json={"phone": phone, "password": "novyy-parol-123"}
    )
    assert new.status_code == 200, new.text
    # Пароль выбрал сам владелец — временным он не считается.
    assert new.json()["mustChangePassword"] is False


async def test_reset_revokes_existing_sessions(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Сессия, открытая до сброса, не переживает его: refresh → 401."""
    phone = _phone()
    temp = await _create_clinic_owner(admin_client, phone)
    login = await client.post(f"{API}/auth/login", json={"phone": phone, "password": temp})
    refresh_token = login.json()["refreshToken"]

    # До сброса токен рабочий.
    alive = await client.post(f"{API}/auth/refresh", json={"refreshToken": refresh_token})
    assert alive.status_code == 200, alive.text

    await client.post(f"{API}/auth/password/forgot", json={"phone": phone})
    reset = await client.post(
        f"{API}/auth/password/reset",
        json={
            "phone": phone,
            "code": await _reset_code(_resources, phone),
            "newPassword": "drugoy-parol-123",
        },
    )
    assert reset.status_code == 200, reset.text

    dead = await client.post(f"{API}/auth/refresh", json={"refreshToken": refresh_token})
    assert dead.status_code == 401, dead.text


async def test_login_code_cannot_reset_password(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Код, выпущенный на вход, пароль не меняет — иначе «продиктуйте код» = угон."""
    phone = _phone()
    temp = await _create_clinic_owner(admin_client, phone)

    requested = await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text
    login_code: str | None = await _resources.fake.get(
        f"auth:otp:code:login:{normalize_phone(phone)}"
    )
    assert login_code is not None

    resp = await client.post(
        f"{API}/auth/password/reset",
        json={"phone": phone, "code": login_code, "newPassword": "chuzhoy-kod-123"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_otp"

    # Прежний пароль на месте.
    still = await client.post(f"{API}/auth/login", json={"phone": phone, "password": temp})
    assert still.status_code == 200, still.text


async def test_reset_code_cannot_log_in(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """И наоборот: кодом на смену пароля не войти."""
    phone = _phone()
    await _create_clinic_owner(admin_client, phone)
    await client.post(f"{API}/auth/password/forgot", json={"phone": phone})

    resp = await client.post(
        f"{API}/auth/otp/verify",
        json={"phone": phone, "code": await _reset_code(_resources, phone)},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "invalid_otp"


async def test_reset_with_wrong_code_is_400(client: AsyncClient, admin_client: AsyncClient) -> None:
    """Неверный код → 400, пароль не тронут."""
    phone = _phone()
    temp = await _create_clinic_owner(admin_client, phone)
    await client.post(f"{API}/auth/password/forgot", json={"phone": phone})

    resp = await client.post(
        f"{API}/auth/password/reset",
        json={"phone": phone, "code": "000000", "newPassword": "ne-proydet-123"},
    )
    assert resp.status_code == 400, resp.text

    still = await client.post(f"{API}/auth/login", json={"phone": phone, "password": temp})
    assert still.status_code == 200, still.text


async def test_forgot_for_unknown_phone_does_not_reveal_it(
    client: AsyncClient, _resources: Resources
) -> None:
    """Незнакомый номер отвечает как знакомый; 404 приходит только после кода."""
    phone = _phone()
    forgot = await client.post(f"{API}/auth/password/forgot", json={"phone": phone})
    assert forgot.status_code == 200, forgot.text
    assert forgot.json()["sent"] is True

    resp = await client.post(
        f"{API}/auth/password/reset",
        json={
            "phone": phone,
            "code": await _reset_code(_resources, phone),
            "newPassword": "nekomu-menyat-123",
        },
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "account_not_found"


async def test_solo_account_has_no_password_to_reset(
    client: AsyncClient, _resources: Resources
) -> None:
    """Соло-врач входит по коду — сбрасывать ему нечего (409 use_otp_login)."""
    phone = _phone()
    await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    login_code: str | None = await _resources.fake.get(
        f"auth:otp:code:login:{normalize_phone(phone)}"
    )
    verify = await client.post(f"{API}/auth/otp/verify", json={"phone": phone, "code": login_code})
    assert verify.status_code == 200, verify.text
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    city = (await client.get(f"{API}/cities")).json()[0]["id"]
    registered = await client.post(
        f"{API}/auth/solo/register",
        json={
            "registrationToken": verify.json()["registrationToken"],
            "fullName": "Соло Без Пароля",
            "specializationId": spec,
            "cityId": city,
        },
    )
    assert registered.status_code == 201, registered.text

    await client.post(f"{API}/auth/password/forgot", json={"phone": phone})
    resp = await client.post(
        f"{API}/auth/password/reset",
        json={
            "phone": phone,
            "code": await _reset_code(_resources, phone),
            "newPassword": "solo-parol-123",
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "use_otp_login"
