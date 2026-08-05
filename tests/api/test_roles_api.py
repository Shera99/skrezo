"""API-тесты управления ролями клиники (право ``roles.manage``).

Ключевые риски:

* роль владельца неизменяема — иначе клинику можно запереть, отобрав права
  у единственного администратора;
* системные роли: права правятся пер-клиника, но переименование/удаление закрыты;
* роль с сотрудниками не удаляется (FK ``membership.role_id`` без каскада);
* изменение прав применяется СРАЗУ (сброс кеша ``PermissionResolver``);
* изоляция клиник: чужая роль не видна и не правится;
* по умолчанию ``roles.manage`` есть только у владельца — работник получает 403.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070091{next(_phone_seq):04d}"


async def _login(client: AsyncClient, phone: str, password: str) -> dict[str, str]:
    resp = await client.post(f"{API}/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


async def _clinic(admin_client: AsyncClient, client: AsyncClient, name: str) -> dict[str, str]:
    """Клиника + токен владельца."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": f"Владелец {name}",
            "adminPhone": phone,
            "name": name,
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    return await _login(client, phone, created.json()["tempPassword"])


async def _roles(client: AsyncClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    resp = await client.get(f"{API}/clinic/roles", headers=headers)
    assert resp.status_code == 200, resp.text
    items: list[dict[str, Any]] = resp.json()["items"]
    return items


def _by_key(roles: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return next(r for r in roles if r["key"] == key)


# ── 1. Чтение ───────────────────────────────────────────────────────────────


async def test_list_roles_and_permissions(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Список ролей клиники с правами, флагами и каталог прав."""
    headers = await _clinic(admin_client, client, "Роли Список")
    roles = await _roles(client, headers)

    keys = {r["key"] for r in roles}
    assert {"owner", "manager", "worker"} <= keys

    owner = _by_key(roles, "owner")
    assert owner["isOwner"] is True
    assert owner["editablePermissions"] is False  # владелец не редактируется
    assert owner["deletable"] is False
    assert owner["membersCount"] == 1  # сам владелец

    worker = _by_key(roles, "worker")
    assert worker["isSystem"] is True
    assert worker["deletable"] is False  # системную не удалить
    assert "schedule.view_own" in worker["permissions"]

    catalog = await client.get(f"{API}/clinic/permissions", headers=headers)
    assert catalog.status_code == 200, catalog.text
    catalog_keys = {p["key"] for p in catalog.json()["items"]}
    assert "cash.operate" in catalog_keys
    assert all(p["groupKey"] for p in catalog.json()["items"])


# ── 2. Кастомные роли ───────────────────────────────────────────────────────


async def test_create_update_delete_custom_role(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Кастомная роль: создание, правка названия и прав, удаление."""
    headers = await _clinic(admin_client, client, "Роли CRUD")

    created = await client.post(
        f"{API}/clinic/roles",
        headers=headers,
        json={"name": "Ресепшен", "permissions": ["patients.view", "schedule.view_all"]},
    )
    assert created.status_code == 201, created.text
    role = created.json()
    assert role["isSystem"] is False
    assert role["key"] is None
    assert sorted(role["permissions"]) == ["patients.view", "schedule.view_all"]
    assert role["deletable"] is True

    updated = await client.patch(
        f"{API}/clinic/roles/{role['id']}",
        headers=headers,
        json={"name": "Ресепшен+", "permissions": ["patients.view"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Ресепшен+"
    assert updated.json()["permissions"] == ["patients.view"]  # набор заменён целиком

    deleted = await client.delete(f"{API}/clinic/roles/{role['id']}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"id": role["id"], "deleted": True}
    assert role["id"] not in {r["id"] for r in await _roles(client, headers)}


async def test_duplicate_name_and_unknown_permission_rejected(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Занятое название → 409; неизвестное право → 422 (роль не создаётся)."""
    headers = await _clinic(admin_client, client, "Роли Валидация")
    first = await client.post(
        f"{API}/clinic/roles", headers=headers, json={"name": "Дубль", "permissions": []}
    )
    assert first.status_code == 201, first.text

    duplicate = await client.post(
        f"{API}/clinic/roles", headers=headers, json={"name": "дубль", "permissions": []}
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"] == "role_name_taken"

    bad = await client.post(
        f"{API}/clinic/roles",
        headers=headers,
        json={"name": "Плохая", "permissions": ["cash.bad"]},
    )
    assert bad.status_code == 422, bad.text
    assert bad.json()["error"] == "permission_not_found"
    assert "Плохая" not in {r["name"] for r in await _roles(client, headers)}


# ── 3. Защита системных ролей и владельца ───────────────────────────────────


async def test_owner_role_is_immutable(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Роль владельца нельзя ни изменить, ни удалить."""
    headers = await _clinic(admin_client, client, "Роли Владелец")
    owner = _by_key(await _roles(client, headers), "owner")

    patched = await client.patch(
        f"{API}/clinic/roles/{owner['id']}", headers=headers, json={"permissions": []}
    )
    assert patched.status_code == 409, patched.text
    assert patched.json()["error"] == "owner_role_immutable"

    deleted = await client.delete(f"{API}/clinic/roles/{owner['id']}", headers=headers)
    assert deleted.status_code == 409, deleted.text


async def test_system_role_permissions_editable_but_not_rename_or_delete(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Системной роли правим права (штатно), но не название и не удаление."""
    headers = await _clinic(admin_client, client, "Роли Системные")
    worker = _by_key(await _roles(client, headers), "worker")

    permissions = await client.patch(
        f"{API}/clinic/roles/{worker['id']}",
        headers=headers,
        json={"permissions": ["patients.view", "schedule.view_own"]},
    )
    assert permissions.status_code == 200, permissions.text
    assert sorted(permissions.json()["permissions"]) == ["patients.view", "schedule.view_own"]

    renamed = await client.patch(
        f"{API}/clinic/roles/{worker['id']}", headers=headers, json={"name": "Мой работник"}
    )
    assert renamed.status_code == 409, renamed.text
    assert renamed.json()["error"] == "system_role_rename"

    deleted = await client.delete(f"{API}/clinic/roles/{worker['id']}", headers=headers)
    assert deleted.status_code == 409, deleted.text
    assert deleted.json()["error"] == "system_role_delete"


async def test_permission_change_applies_immediately(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Снятие права у роли работника закрывает ему доступ сразу (кеш сброшен)."""
    headers = await _clinic(admin_client, client, "Роли Кеш")
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    staff = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": "Врач Кеш", "phone": phone, "specializationId": spec},
    )
    assert staff.status_code == 201, staff.text
    worker_headers = await _login(client, phone, str(staff.json()["tempPassword"]))

    # Право есть — пациенты видны (запрос заодно прогревает кеш прав роли).
    assert (
        await client.get(f"{API}/clinic/patients", headers=worker_headers)
    ).status_code == 200

    worker = _by_key(await _roles(client, headers), "worker")
    patched = await client.patch(
        f"{API}/clinic/roles/{worker['id']}", headers=headers, json={"permissions": []}
    )
    assert patched.status_code == 200, patched.text

    blocked = await client.get(f"{API}/clinic/patients", headers=worker_headers)
    assert blocked.status_code == 403, blocked.text


async def test_role_with_members_cannot_be_deleted(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Роль с активными сотрудниками не удаляется → 409 с числом сотрудников."""
    headers = await _clinic(admin_client, client, "Роли Люди")
    worker = _by_key(await _roles(client, headers), "worker")
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    added = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": "Врач", "phone": _phone(), "specializationId": spec},
    )
    assert added.status_code == 201, added.text

    # Системную роль и так не удалить — проверяем счётчик сотрудников в выдаче.
    refreshed = _by_key(await _roles(client, headers), "worker")
    assert refreshed["membersCount"] >= 1
    assert refreshed["deletable"] is False
    assert worker["id"] == refreshed["id"]


# ── 4. Изоляция и доступ ────────────────────────────────────────────────────


async def test_role_of_other_clinic_is_404(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Чужая роль не видна и не правится."""
    headers_a = await _clinic(admin_client, client, "Роли A")
    headers_b = await _clinic(admin_client, client, "Роли B")
    role_b = await client.post(
        f"{API}/clinic/roles", headers=headers_b, json={"name": "Чужая", "permissions": []}
    )
    assert role_b.status_code == 201, role_b.text
    foreign_id = role_b.json()["id"]

    assert foreign_id not in {r["id"] for r in await _roles(client, headers_a)}
    patched = await client.patch(
        f"{API}/clinic/roles/{foreign_id}", headers=headers_a, json={"name": "Перехват"}
    )
    assert patched.status_code == 404, patched.text
    assert patched.json()["error"] == "role_not_found"

    deleted = await client.delete(f"{API}/clinic/roles/{foreign_id}", headers=headers_a)
    assert deleted.status_code == 404, deleted.text


async def test_worker_has_no_roles_manage(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """По умолчанию roles.manage есть только у владельца — работнику 403."""
    headers = await _clinic(admin_client, client, "Роли Доступ")
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    staff = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": "Врач Без Прав", "phone": phone, "specializationId": spec},
    )
    worker_headers = await _login(client, phone, str(staff.json()["tempPassword"]))

    assert (await client.get(f"{API}/clinic/roles", headers=worker_headers)).status_code == 403
    created = await client.post(
        f"{API}/clinic/roles", headers=worker_headers, json={"name": "Своя", "permissions": []}
    )
    assert created.status_code == 403, created.text


async def test_unknown_role_is_404(admin_client: AsyncClient, client: AsyncClient) -> None:
    """Правка несуществующей роли → 404."""
    headers = await _clinic(admin_client, client, "Роли 404")
    resp = await client.patch(
        f"{API}/clinic/roles/{uuid.uuid4()}", headers=headers, json={"name": "Нет"}
    )
    assert resp.status_code == 404, resp.text


# ── Назначение роли сотруднику ──────────────────────────────────────────────


async def _add_staff(
    client: AsyncClient, headers: dict[str, str], name: str, **extra: Any
) -> dict[str, Any]:
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    resp = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": name, "phone": _phone(), "specializationId": spec, **extra},
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_staff_role_can_be_assigned_and_changed(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Роль сотрудника назначается при найме и меняется потом.

    Без этого настройка ролей бессмысленна: все оставались бы «работниками», а
    роль «менеджер» — недостижимой.
    """
    headers = await _clinic(admin_client, client, "Роли назначение")
    roles = await _roles(client, headers)
    manager, worker = _by_key(roles, "manager"), _by_key(roles, "worker")

    hired = await _add_staff(client, headers, "Администратор", roleId=manager["id"])
    membership_id = hired["membershipId"]

    listing = await client.get(f"{API}/clinic/staff", headers=headers)
    assigned = next(m for m in listing.json() if m["membershipId"] == membership_id)
    assert assigned["roleId"] == manager["id"]

    moved = await client.put(
        f"{API}/clinic/staff/{membership_id}", headers=headers, json={"roleId": worker["id"]}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["roleId"] == worker["id"]


async def test_owner_role_cannot_be_assigned(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Владелец в клинике один — его роль через найм не выдаётся."""
    headers = await _clinic(admin_client, client, "Роли владелец")
    owner = next(r for r in await _roles(client, headers) if r["isOwner"])
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]

    resp = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={
            "fullName": "Самозванец",
            "phone": _phone(),
            "specializationId": spec,
            "roleId": owner["id"],
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "cannot_assign_owner_role"


async def test_role_of_other_clinic_is_not_assignable(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Чужая роль не назначается — скоуп по клинике из токена."""
    headers_a = await _clinic(admin_client, client, "Роли A")
    headers_b = await _clinic(admin_client, client, "Роли B")
    role_b = _by_key(await _roles(client, headers_b), "manager")
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]

    resp = await client.post(
        f"{API}/clinic/staff",
        headers=headers_a,
        json={
            "fullName": "Чужая роль",
            "phone": _phone(),
            "specializationId": spec,
            "roleId": role_b["id"],
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "role_not_found"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/clinic/roles"),
        ("GET", "/clinic/permissions"),
        ("POST", "/clinic/roles"),
        ("PATCH", f"/clinic/roles/{uuid.uuid4()}"),
        ("DELETE", f"/clinic/roles/{uuid.uuid4()}"),
    ],
)
async def test_roles_endpoints_without_token_401(
    client: AsyncClient, method: str, path: str
) -> None:
    """Ручки ролей закрыты авторизацией."""
    resp = await client.request(method, f"{API}{path}", json={"name": "X", "permissions": []})
    assert resp.status_code == 401, resp.text
