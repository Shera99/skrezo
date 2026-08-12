"""API-тесты отзыва доступа при удалении сотрудника и смене его роли.

Скоуп клиники и роль приходят в claims токена, а токен выписывается один раз:
refresh живёт 30 дней. Поэтому «удалили сотрудника» обязано означать не только
строку в БД — иначе уволенный работает дальше, пока сам не выйдет из приложения.

Ключевые риски:

* уже выданный токен уволенного не должен пускать на эндпоинты клиники —
  ни на `require_permission`, ни на скоуп-гварды расписания/кассы/отчётов;
* refresh не должен воскрешать доступ: он выпишет новый access с прежними
  claims, и тот тоже обязан упираться в 403;
* смена роли должна применяться сразу, а не после перелогина;
* аккаунт при этом сохраняется: он глобальный, и человек может работать
  в другой клинике.
"""

from __future__ import annotations

import itertools
from datetime import date
from typing import Any

from httpx import AsyncClient

from tests.conftest import API, otp_code, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070094{next(_phone_seq):04d}"


async def _clinic(admin_client: AsyncClient, client: AsyncClient) -> dict[str, str]:
    """Клиника с владельцем; вернуть заголовки владельца."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Отзыва",
            "adminPhone": phone,
            "name": f"Клиника Отзыва {phone}",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    return await otp_login(client, phone)


async def _worker(
    client: AsyncClient, owner: dict[str, str]
) -> tuple[str, str, dict[str, str]]:
    """Добавить работника; вернуть (membershipId, телефон, его заголовки)."""
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    added = await client.post(
        f"{API}/clinic/staff",
        headers=owner,
        json={"fullName": "Работник Отзыва", "phone": phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    return str(added.json()["membershipId"]), phone, await otp_login(client, phone)


async def _reads(client: AsyncClient, headers: dict[str, str]) -> dict[str, int]:
    """Два чтения, покрывающие обе семьи гвардов: require_permission и скоуп."""
    services = await client.get(f"{API}/clinic/services", headers=headers)
    day = await client.get(
        f"{API}/clinic/schedule/day?date={date.today().isoformat()}", headers=headers
    )
    return {"services": services.status_code, "day": day.status_code}


# ── Удаление закрывает доступ ───────────────────────────────────────────────


async def test_removed_worker_loses_access_immediately(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Токен, выданный до удаления, перестаёт работать сразу же."""
    owner = await _clinic(admin_client, client)
    membership_id, _, worker = await _worker(client, owner)

    assert await _reads(client, worker) == {"services": 200, "day": 200}

    removed = await client.delete(f"{API}/clinic/staff/{membership_id}", headers=owner)
    assert removed.status_code == 200, removed.text

    assert await _reads(client, worker) == {"services": 403, "day": 403}
    denied = await client.get(f"{API}/clinic/services", headers=worker)
    assert denied.json()["error"] == "membership_revoked"


async def test_removal_response_states_what_happened(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Ответ говорит и про доступ, и про судьбу аккаунта — не только «ok»."""
    owner = await _clinic(admin_client, client)
    membership_id, _, _ = await _worker(client, owner)

    removed = await client.delete(f"{API}/clinic/staff/{membership_id}", headers=owner)
    assert removed.status_code == 200, removed.text
    body = removed.json()
    assert body == {
        "ok": True,
        "mode": "deactivated",
        "accessRevoked": True,
        "accountKept": True,
    }


async def test_admin_side_removal_closes_access_too(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Удаление врача из админки Skrezo закрывает доступ так же, как из кабинета."""
    owner = await _clinic(admin_client, client)
    clinic_id = (await client.get(f"{API}/auth/me", headers=owner)).json()["activeClinic"][
        "clinicId"
    ]
    membership_id, _, worker = await _worker(client, owner)

    removed = await admin_client.delete(
        f"{API}/admin/clients/{clinic_id}/doctors/{membership_id}"
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["accessRevoked"] is True

    assert await _reads(client, worker) == {"services": 403, "day": 403}


async def test_refresh_does_not_resurrect_access(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Refresh выпишет новый access с прежними claims — он тоже не пускает.

    Самый живучий сценарий: клиент уволенного продолжает обновлять токен и без
    этой проверки работал бы до конца жизни refresh-токена, то есть месяц.
    """
    owner = await _clinic(admin_client, client)
    membership_id, phone, _ = await _worker(client, owner)

    requested = await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text
    verified = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await otp_code(phone)}
    )
    refresh_token = verified.json()["auth"]["refreshToken"]

    await client.delete(f"{API}/clinic/staff/{membership_id}", headers=owner)

    refreshed = await client.post(f"{API}/auth/refresh", json={"refreshToken": refresh_token})
    assert refreshed.status_code == 200, refreshed.text  # сессия аккаунта жива
    headers = {"Authorization": f"Bearer {refreshed.json()['accessToken']}"}
    assert await _reads(client, headers) == {"services": 403, "day": 403}


async def test_removed_worker_can_still_log_in_without_clinic(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Аккаунт сохраняется: вход по коду работает, но клиники в токене нет."""
    owner = await _clinic(admin_client, client)
    membership_id, phone, _ = await _worker(client, owner)
    await client.delete(f"{API}/clinic/staff/{membership_id}", headers=owner)

    headers = await otp_login(client, phone)
    me = await client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["clinics"] == []
    assert me.json()["activeClinic"] is None
    assert me.json()["permissions"] == []


async def test_me_shows_no_permissions_on_stale_token(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """У старого токена /auth/me не должен показывать права, которых уже нет.

    Фронт рисует меню по этому списку: оставить его полным — значит показать
    уволенному интерфейс, где каждая кнопка отвечает 403.
    """
    owner = await _clinic(admin_client, client)
    membership_id, _, worker = await _worker(client, owner)
    assert (await client.get(f"{API}/auth/me", headers=worker)).json()["permissions"]

    await client.delete(f"{API}/clinic/staff/{membership_id}", headers=owner)

    me = await client.get(f"{API}/auth/me", headers=worker)
    assert me.status_code == 200, me.text
    assert me.json()["activeClinic"] is None
    assert me.json()["permissions"] == []


# ── Смена роли применяется сразу ────────────────────────────────────────────


async def test_role_change_applies_to_existing_token(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Повышение до менеджера действует без перелогина: роль берётся из БД."""
    owner = await _clinic(admin_client, client)
    membership_id, _, worker = await _worker(client, owner)

    # Работнику журнал клиники не положен, менеджеру — положен.
    assert (await client.get(f"{API}/clinic/audit", headers=worker)).status_code == 403

    roles = (await client.get(f"{API}/clinic/roles", headers=owner)).json()["items"]
    manager: dict[str, Any] = next(r for r in roles if r["key"] == "manager")
    updated = await client.put(
        f"{API}/clinic/staff/{membership_id}", headers=owner, json={"roleId": manager["id"]}
    )
    assert updated.status_code == 200, updated.text

    assert (await client.get(f"{API}/clinic/audit", headers=worker)).status_code == 200
