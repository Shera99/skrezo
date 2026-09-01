"""Тесты Ф4 (сокет): авторизация комнат и шина сигналов.

Сам Socket.IO-транспорт (handshake, пинги) — библиотечный и здесь не
проверяется; проверяется НАША логика, где живут дыры:

* комнаты определяет сервер по живым членствам — чужая клиника недостижима
  в принципе (обработчиков подписки не существует), уволенный комнат не имеет;
* мусорный или пустой токен не подключается;
* публикация сигнала — обычный pg_notify в транзакции (smoke).
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.errors import UnauthorizedError
from app.realtime.server import authorize_rooms, room_of
from app.shared.db.models.account import Account
from app.shared.db.models.membership import Membership
from app.shared.db.models.role import Role
from app.shared.domain.value_objects import normalize_phone
from app.shared.realtime import PgNotifyRealtimeGateway
from tests.conftest import API, Resources, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070079{next(_phone_seq):04d}"


def _token(headers: dict[str, str]) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


async def _make_clinic(
    admin_client: AsyncClient, client: AsyncClient, name: str
) -> tuple[str, dict[str, str]]:
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
    return str(created.json()["id"]), await otp_login(client, phone)


async def _add_staff(
    client: AsyncClient, owner_headers: dict[str, str], phone: str, name: str
) -> Any:
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    return await client.post(
        f"{API}/clinic/staff",
        headers=owner_headers,
        json={"fullName": name, "phone": phone, "specializationId": spec},
    )


async def test_rooms_follow_live_memberships(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Комнаты — по живым членствам: своя клиника есть, уволенный — без комнат."""
    clinic_id, owner_headers = await _make_clinic(admin_client, client, "Клиника Сокет")

    # Сотрудник клиники: получает комнату своей клиники.
    staff_phone = _phone()
    added = await _add_staff(client, owner_headers, staff_phone, "Сокет Сотрудник")
    assert added.status_code == 201, added.text
    membership_id = added.json()["membershipId"]
    staff_headers = await otp_login(client, staff_phone)

    async with _resources.factory() as session:
        rooms = await authorize_rooms(session, _token(staff_headers))
    assert rooms == [room_of(clinic_id)]

    # Уволили — комнат больше нет: чужие сигналы бывшему сотруднику не идут.
    removed = await client.delete(
        f"{API}/clinic/staff/{membership_id}", headers=owner_headers
    )
    assert removed.status_code in (200, 204), removed.text
    async with _resources.factory() as session:
        rooms = await authorize_rooms(session, _token(staff_headers))
    assert rooms == []


async def test_two_clinics_mean_two_rooms(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Сотрудник двух клиник подписан на обе комнаты (§9)."""
    clinic_a, owner_a = await _make_clinic(admin_client, client, "Сокет-А")
    clinic_b, _owner_b = await _make_clinic(admin_client, client, "Сокет-Б")

    shared_phone = _phone()
    first = await _add_staff(client, owner_a, shared_phone, "Два Кабинета")
    assert first.status_code == 201, first.text
    # Ручка staff не подхватывает существующий аккаунт (phone_already_used) —
    # второе членство создаём напрямую: тут проверяется шина, а не онбординг.
    async with _resources.factory() as session:
        account_id = (
            await session.execute(
                select(Account.id).where(Account.phone == normalize_phone(shared_phone))
            )
        ).scalar_one()
        role_id = (
            await session.execute(
                select(Role.id).where(
                    Role.clinic_id == uuid.UUID(clinic_b), Role.key == "worker"
                )
            )
        ).scalar_one()
        session.add(
            Membership(
                account_id=account_id, clinic_id=uuid.UUID(clinic_b), role_id=role_id
            )
        )
        await session.commit()

    headers = await otp_login(client, shared_phone)
    async with _resources.factory() as session:
        rooms = await authorize_rooms(session, _token(headers))
    assert set(rooms) == {room_of(clinic_a), room_of(clinic_b)}


async def test_bad_or_missing_token_rejected(_resources: Resources) -> None:
    """Мусорный и пустой токены не подключаются — как в REST."""
    async with _resources.factory() as session:
        with pytest.raises(UnauthorizedError):
            await authorize_rooms(session, "garbage.token.here")
        with pytest.raises(UnauthorizedError):
            await authorize_rooms(session, None)


async def test_publish_gateway_smoke(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Гейтвей шины публикует pg_notify из транзакции без ошибок."""
    clinic_id, _headers = await _make_clinic(admin_client, client, "Сокет-Шина")
    async with _resources.factory() as session:
        gateway = PgNotifyRealtimeGateway(session)
        await gateway.emit(uuid.UUID(clinic_id), "inbox.new", {"priority": "normal"})
        await session.commit()
