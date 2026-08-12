"""API-тесты админских списков людей: сотрудники клиники и все аккаунты.

Обе ручки нужны, потому что до них админ Skrezo вообще не видел сотрудников
клиник иначе как внутри карточки клиента, а исключённых не отличал от
работающих.

Ключевые риски:

* исключённый обязан остаться в выдаче с `active: false` — на его членстве
  висят приёмы и платежи, и по списку должно быть видно, кто их вёл;
* строка списка аккаунтов = человек, а не членство: врач из двух клиник не
  должен дублироваться, иначе `totalCount` перестаёт отвечать на вопрос
  «сколько всего аккаунтов»;
* фильтры не должны врать: `active=false` без клиники — «нигде не работает»,
  с клиникой — «исключён именно из неё».
"""

from __future__ import annotations

import itertools
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text

from app.shared.domain.value_objects import normalize_phone
from tests.conftest import API, Resources, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070095{next(_phone_seq):04d}"


async def _clinic(admin_client: AsyncClient, name: str) -> tuple[str, str]:
    """Клиника с владельцем; вернуть (clinicId, телефон владельца)."""
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
    return str(created.json()["id"]), phone


async def _doctor(admin_client: AsyncClient, clinic_id: str, name: str) -> tuple[str, str]:
    """Врач в клинике (админ-сайд); вернуть (membershipId, телефон)."""
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]
    phone = _phone()
    added = await admin_client.post(
        f"{API}/admin/clients/{clinic_id}/doctors",
        json={"fullName": name, "phone": phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    return str(added.json()["id"]), phone


# ── GET /admin/clients/{id}/staff ───────────────────────────────────────────


async def test_clinic_staff_lists_owner_and_doctors(admin_client: AsyncClient) -> None:
    """Владелец идёт первым (role=admin), дальше врачи — все с active."""
    clinic_id, owner_phone = await _clinic(admin_client, "Клиника Состава")
    _, doctor_phone = await _doctor(admin_client, clinic_id, "Врач Первый")

    resp = await admin_client.get(f"{API}/admin/clients/{clinic_id}/staff")
    assert resp.status_code == 200, resp.text
    staff: list[dict[str, Any]] = resp.json()

    assert [s["role"] for s in staff] == ["admin", "doctor"]
    assert all(s["active"] is True for s in staff)
    assert {s["phone"] for s in staff} == {
        f"+996{owner_phone[1:]}",
        f"+996{doctor_phone[1:]}",
    }
    assert staff[1]["specialization"]


async def test_clinic_staff_keeps_removed_with_active_false(admin_client: AsyncClient) -> None:
    """Исключённый остаётся в списке помеченным — иначе непонятно, кто вёл приёмы."""
    clinic_id, _ = await _clinic(admin_client, "Клиника Исключения")
    doctor_id, _ = await _doctor(admin_client, clinic_id, "Врач Уволенный")

    removed = await admin_client.delete(f"{API}/admin/clients/{clinic_id}/doctors/{doctor_id}")
    assert removed.status_code == 200, removed.text

    staff = (await admin_client.get(f"{API}/admin/clients/{clinic_id}/staff")).json()
    doctor = next(s for s in staff if s["id"] == doctor_id)
    assert doctor["active"] is False


async def test_clinic_staff_active_filter(admin_client: AsyncClient) -> None:
    """`?active=` разводит работающих и исключённых."""
    clinic_id, _ = await _clinic(admin_client, "Клиника Фильтра")
    kept_id, _ = await _doctor(admin_client, clinic_id, "Врач Оставшийся")
    gone_id, _ = await _doctor(admin_client, clinic_id, "Врач Ушедший")
    await admin_client.delete(f"{API}/admin/clients/{clinic_id}/doctors/{gone_id}")

    working = (await admin_client.get(f"{API}/admin/clients/{clinic_id}/staff?active=true")).json()
    assert {s["id"] for s in working} == {kept_id} | {
        s["id"] for s in working if s["role"] == "admin"
    }
    assert gone_id not in {s["id"] for s in working}

    excluded = (
        await admin_client.get(f"{API}/admin/clients/{clinic_id}/staff?active=false")
    ).json()
    assert [s["id"] for s in excluded] == [gone_id]


async def test_clinic_staff_unknown_clinic_is_404(admin_client: AsyncClient) -> None:
    """Опечатка в clinicId не должна выглядеть как «клиника без сотрудников»."""
    resp = await admin_client.get(
        f"{API}/admin/clients/00000000-0000-4000-8000-000000000000/staff"
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "client_not_found"


async def test_clinic_staff_needs_platform_admin(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Владелец клиники в админскую ручку не ходит — у него свой /clinic/staff."""
    clinic_id, owner_phone = await _clinic(admin_client, "Клиника Доступа")
    owner = await otp_login(client, owner_phone)

    resp = await client.get(f"{API}/admin/clients/{clinic_id}/staff", headers=owner)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "not_platform_admin"


# ── GET /admin/accounts ─────────────────────────────────────────────────────


async def test_accounts_list_shows_where_person_works(admin_client: AsyncClient) -> None:
    """Строка аккаунта несёт клиники вложенным списком, а не плоско."""
    clinic_id, _ = await _clinic(admin_client, "Клиника Аккаунтов")
    _, doctor_phone = await _doctor(admin_client, clinic_id, "Врач Списочный")

    resp = await admin_client.get(f"{API}/admin/accounts?query={doctor_phone}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["currentPage"] == 1
    assert body["pageSize"] == 25
    assert body["totalCount"] == 1

    account = body["list"][0]
    assert account["phone"] == f"+996{doctor_phone[1:]}"
    assert account["isPlatformAdmin"] is False
    assert account["hasActiveMembership"] is True
    assert len(account["memberships"]) == 1
    membership = account["memberships"][0]
    assert membership["clinicId"] == clinic_id
    assert membership["clinicName"] == "Клиника Аккаунтов"
    assert membership["isOwner"] is False
    assert membership["active"] is True


async def test_accounts_list_marks_removed_person(admin_client: AsyncClient) -> None:
    """После исключения человек остаётся в списке, но помечен неработающим."""
    clinic_id, _ = await _clinic(admin_client, "Клиника Метки")
    doctor_id, doctor_phone = await _doctor(admin_client, clinic_id, "Врач Меченый")
    await admin_client.delete(f"{API}/admin/clients/{clinic_id}/doctors/{doctor_id}")

    account = (
        await admin_client.get(f"{API}/admin/accounts?query={doctor_phone}")
    ).json()["list"][0]
    assert account["hasActiveMembership"] is False
    assert account["memberships"][0]["active"] is False


async def test_person_in_two_clinics_is_one_row(
    admin_client: AsyncClient, _resources: Resources
) -> None:
    """Врач в двух клиниках — одна строка с двумя членствами.

    Плоский список дублировал бы его, и `totalCount` перестал бы отвечать на
    вопрос «сколько всего аккаунтов». Ради этого случая строка и сделана
    аккаунтом, а не членством.
    """
    first_id, _ = await _clinic(admin_client, "Клиника Одна")
    second_id, _ = await _clinic(admin_client, "Клиника Две")
    _, phone = await _doctor(admin_client, first_id, "Врач Двойной")

    # Через API того же человека во вторую клинику не добавить — дедуп телефона
    # (`phone_already_used`), поэтому второе членство заводим прямо в БД.
    async with _resources.factory() as session:
        account_id = (
            await session.execute(
                text("SELECT id FROM account WHERE phone = :phone"),
                {"phone": normalize_phone(phone)},
            )
        ).scalar_one()
        role_id = (
            await session.execute(
                text("SELECT id FROM role WHERE clinic_id = :clinic AND key = 'worker'"),
                {"clinic": second_id},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO membership (id, clinic_id, account_id, role_id, active) "
                "VALUES (gen_random_uuid(), :clinic, :account, :role, true)"
            ),
            {"clinic": second_id, "account": account_id, "role": role_id},
        )
        await session.commit()

    accounts = (await admin_client.get(f"{API}/admin/accounts?query={phone}")).json()
    assert accounts["totalCount"] == 1, "человек не должен дублироваться по клиникам"
    memberships = accounts["list"][0]["memberships"]
    assert {m["clinicId"] for m in memberships} == {first_id, second_id}
    assert all(m["active"] for m in memberships)


async def test_removed_from_one_clinic_still_works_in_another(
    admin_client: AsyncClient, _resources: Resources
) -> None:
    """Исключение из одной клиники не трогает вторую — и это видно по строке."""
    first_id, _ = await _clinic(admin_client, "Клиника Ухода")
    second_id, _ = await _clinic(admin_client, "Клиника Остатка")
    doctor_id, phone = await _doctor(admin_client, first_id, "Врач Кочующий")

    async with _resources.factory() as session:
        account_id = (
            await session.execute(
                text("SELECT id FROM account WHERE phone = :phone"),
                {"phone": normalize_phone(phone)},
            )
        ).scalar_one()
        role_id = (
            await session.execute(
                text("SELECT id FROM role WHERE clinic_id = :clinic AND key = 'worker'"),
                {"clinic": second_id},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO membership (id, clinic_id, account_id, role_id, active) "
                "VALUES (gen_random_uuid(), :clinic, :account, :role, true)"
            ),
            {"clinic": second_id, "account": account_id, "role": role_id},
        )
        await session.commit()

    await admin_client.delete(f"{API}/admin/clients/{first_id}/doctors/{doctor_id}")

    account = (
        await admin_client.get(f"{API}/admin/accounts?query={phone}")
    ).json()["list"][0]
    assert account["hasActiveMembership"] is True  # вторая клиника осталась
    by_clinic = {m["clinicId"]: m["active"] for m in account["memberships"]}
    assert by_clinic == {first_id: False, second_id: True}


async def test_accounts_clinic_filter_keeps_removed(admin_client: AsyncClient) -> None:
    """Фильтр по клинике показывает и её уволенных — их надо где-то искать."""
    clinic_id, _ = await _clinic(admin_client, "Клиника Фильтра Клиники")
    doctor_id, _ = await _doctor(admin_client, clinic_id, "Врач Выбывший")
    await admin_client.delete(f"{API}/admin/clients/{clinic_id}/doctors/{doctor_id}")

    everyone = (await admin_client.get(f"{API}/admin/accounts?clinicId={clinic_id}")).json()
    assert everyone["totalCount"] == 2  # владелец + уволенный врач

    excluded = (
        await admin_client.get(f"{API}/admin/accounts?clinicId={clinic_id}&active=false")
    ).json()
    assert excluded["totalCount"] == 1
    assert excluded["list"][0]["memberships"][0]["active"] is False

    working = (
        await admin_client.get(f"{API}/admin/accounts?clinicId={clinic_id}&active=true")
    ).json()
    assert working["totalCount"] == 1
    assert working["list"][0]["memberships"][0]["isOwner"] is True


async def test_accounts_platform_admin_filter(admin_client: AsyncClient) -> None:
    """Команда Skrezo отделяется от клиентских аккаунтов одним флагом."""
    clinic_id, _ = await _clinic(admin_client, "Клиника Разделения")
    await _doctor(admin_client, clinic_id, "Врач Обычный")

    team = (await admin_client.get(f"{API}/admin/accounts?isPlatformAdmin=true")).json()
    assert team["totalCount"] == 1  # сам тестовый админ
    admin = team["list"][0]
    assert admin["isPlatformAdmin"] is True
    # Членств у команды нет по устройству — отсюда и false в сводке.
    assert admin["memberships"] == []
    assert admin["hasActiveMembership"] is False

    clients = (await admin_client.get(f"{API}/admin/accounts?isPlatformAdmin=false")).json()
    assert clients["totalCount"] == 2  # владелец + врач
    assert all(a["isPlatformAdmin"] is False for a in clients["list"])


async def test_accounts_needs_platform_admin(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Список всех людей платформы — только для команды Skrezo."""
    _, owner_phone = await _clinic(admin_client, "Клиника Чужая")
    owner = await otp_login(client, owner_phone)

    resp = await client.get(f"{API}/admin/accounts", headers=owner)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "not_platform_admin"
