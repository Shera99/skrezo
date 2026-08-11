"""API-тесты настроек своей клиники (`settings.view` / `settings.edit`).

Клиника ведёт себя сама, но не всё: тариф и заметки саппорта остаются за Skrezo.
Отсюда риски:

* **таймзона** — самое ответственное поле: в ней считаются лента дня, слоты и
  периоды отчётов, поэтому битая зона обязана отвергаться, а не откатываться
  молча на дефолт;
* `type`/`seatsLimit` (тариф) и `supportNotes` (внутренние заметки) не должны
  ни отдаваться на правку, ни утекать в выдачу;
* RBAC: у врача нет `settings.view`/`settings.edit`;
* изоляция клиник: ручка работает только с клиникой из токена.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070099{next(_phone_seq):04d}"


@pytest.fixture
async def owner(admin_client: AsyncClient, client: AsyncClient) -> dict[str, str]:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Настройки",
            "adminPhone": phone,
            "name": "Клиника Настройки",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    return await otp_login(client, phone)


async def test_owner_sees_own_settings(client: AsyncClient, owner: dict[str, str]) -> None:
    """Карточка отдаёт всё нужное клинике — и ничего внутреннего Skrezo."""
    resp = await client.get(f"{API}/clinic/settings", headers=owner)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Клиника Настройки"
    assert body["timezone"] == "Asia/Bishkek"
    assert body["cityName"]  # название города на языке запроса
    assert body["seatsLimit"] == 5
    assert "supportNotes" not in body


async def test_timezone_change_moves_the_day(client: AsyncClient, owner: dict[str, str]) -> None:
    """Смена таймзоны — рабочий сценарий: клиника переехала, день считается иначе."""
    resp = await client.patch(
        f"{API}/clinic/settings", headers=owner, json={"timezone": "Asia/Almaty"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["timezone"] == "Asia/Almaty"

    day = await client.get(f"{API}/clinic/schedule/day?date=2026-07-20", headers=owner)
    assert day.status_code == 200, day.text
    assert day.json()["timezone"] == "Asia/Almaty"


async def test_invalid_timezone_is_rejected(client: AsyncClient, owner: dict[str, str]) -> None:
    """Опечатка в зоне не должна молча уводить расписание в дефолт."""
    resp = await client.patch(
        f"{API}/clinic/settings", headers=owner, json={"timezone": "Asia/Bishkeck"}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_timezone"

    unchanged = await client.get(f"{API}/clinic/settings", headers=owner)
    assert unchanged.json()["timezone"] == "Asia/Bishkek"


async def test_contacts_and_city_are_editable(
    admin_client: AsyncClient, client: AsyncClient, owner: dict[str, str]
) -> None:
    """Название, адрес, телефон и город правятся; язык тоже."""
    cities = (await admin_client.get(f"{API}/admin/refs/cities")).json()
    other_city = cities[-1]["id"]
    resp = await client.patch(
        f"{API}/clinic/settings",
        headers=owner,
        json={
            "name": "Клиника «Улыбка»",
            "address": "Чуй 100",
            "businessPhone": "0555111222",
            "cityId": other_city,
            "lang": "kg",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Клиника «Улыбка»"
    assert body["address"] == "Чуй 100"
    assert body["businessPhone"] == "0555111222"
    assert body["cityId"] == other_city
    assert body["lang"] == "kg"


async def test_unknown_city_is_rejected(client: AsyncClient, owner: dict[str, str]) -> None:
    """Город — из справочника, а не произвольный id."""
    resp = await client.patch(
        f"{API}/clinic/settings", headers=owner, json={"cityId": str(uuid.uuid4())}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "city_not_found"


async def test_tariff_fields_are_not_editable(client: AsyncClient, owner: dict[str, str]) -> None:
    """Тариф и заметки саппорта клиника себе не выпишет: поля игнорируются."""
    resp = await client.patch(
        f"{API}/clinic/settings",
        headers=owner,
        json={"seatsLimit": 500, "type": "solo", "supportNotes": "мы хорошие"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["seatsLimit"] == 5
    assert body["type"] == "clinic"


@pytest.mark.parametrize("field", ["name", "timezone", "lang"])
async def test_not_null_fields_reject_null(
    client: AsyncClient, owner: dict[str, str], field: str
) -> None:
    """NOT NULL-поля не обнуляются: пустая таймзона сломала бы всё расписание."""
    resp = await client.patch(f"{API}/clinic/settings", headers=owner, json={field: None})
    assert resp.status_code == 422, resp.text


async def test_worker_has_no_access(client: AsyncClient, owner: dict[str, str]) -> None:
    """У врача нет settings.view/settings.edit — настройки клиники не его дело."""
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    worker = await client.post(
        f"{API}/clinic/staff",
        headers=owner,
        json={"fullName": "Врач", "phone": phone, "specializationId": spec},
    )
    assert worker.status_code == 201, worker.text
    worker_headers = await otp_login(client, phone)

    assert (await client.get(f"{API}/clinic/settings", headers=worker_headers)).status_code == 403
    patched = await client.patch(
        f"{API}/clinic/settings", headers=worker_headers, json={"name": "Моя клиника"}
    )
    assert patched.status_code == 403, patched.text


@pytest.mark.parametrize(("method", "body"), [("GET", None), ("PATCH", {"name": "X"})])
async def test_settings_without_token_is_401(
    client: AsyncClient, method: str, body: dict[str, Any] | None
) -> None:
    """Ручка закрыта авторизацией."""
    resp = await client.request(method, f"{API}/clinic/settings", json=body)
    assert resp.status_code == 401, resp.text
