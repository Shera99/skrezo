"""API-тесты пуш-фундамента (P0-a): устройства и настройки уведомлений.

Транспорта и диспетчера здесь ещё нет (следующий инкремент) — проверяем только
три ручки, которые зовёт приложение, и их инварианты:

* устройство привязано к членству, а ``token`` уникален на платформу: повторная
  регистрация того же токена — обновление на месте, а не дубль;
* снятие устройства идемпотентно и трогает только СВОё (владение по аккаунту из
  токена) — чужой токен снять нельзя, даже зная его;
* префы: ``disabled`` — NOT NULL (пустой список ``[]``, а не ``null``); тихие
  часы обнуляются явным ``null``; строка префов заводится при первой правке;
* доступ — любому аутентифицированному члену клиники, без отдельного права
  (в отличие от настроек клиники): устройство и префы и так его собственные.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, otp_login

_phone_seq = itertools.count(1)
_token_seq = itertools.count(1)


def _phone() -> str:
    return f"070088{next(_phone_seq):04d}"


def _token() -> str:
    """Уникальный токен на тест: таблица device не чистится явно между тестами
    (уходит каскадом при удалении клиники/аккаунта), а ``token`` — UNIQUE."""
    return f"tok-{next(_token_seq):06d}"


async def _make_owner(admin_client: AsyncClient, client: AsyncClient, name: str) -> dict[str, str]:
    """Завести клинику через админку и войти её владельцем — вернуть заголовок."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": name,
            "adminPhone": phone,
            "name": f"Клиника {name}",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    return await otp_login(client, phone)


@pytest.fixture
async def owner(admin_client: AsyncClient, client: AsyncClient) -> dict[str, str]:
    return await _make_owner(admin_client, client, "Пуш")


# ── Устройства ───────────────────────────────────────────────────────────────


async def test_register_returns_device(client: AsyncClient, owner: dict[str, str]) -> None:
    """Регистрация возвращает карточку устройства в camelCase."""
    token = _token()
    resp = await client.post(
        f"{API}/clinic/devices",
        headers=owner,
        json={"token": token, "platform": "android", "appVersion": "1.4.0", "locale": "ru"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"] == token
    assert body["platform"] == "android"
    assert body["appVersion"] == "1.4.0"
    assert body["locale"] == "ru"
    assert body["id"] and body["createdAt"] and body["lastSeenAt"]


async def test_reregister_same_token_updates_in_place(
    client: AsyncClient, owner: dict[str, str]
) -> None:
    """Тот же токен второй раз — обновление той же записи, а не дубль."""
    token = _token()
    first = await client.post(
        f"{API}/clinic/devices",
        headers=owner,
        json={"token": token, "platform": "ios", "appVersion": "1.0.0", "locale": "ru"},
    )
    assert first.status_code == 200, first.text
    device_id = first.json()["id"]

    again = await client.post(
        f"{API}/clinic/devices",
        headers=owner,
        json={"token": token, "platform": "ios", "appVersion": "2.0.0", "locale": "kg"},
    )
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["id"] == device_id  # та же строка — не завели второе устройство
    assert body["appVersion"] == "2.0.0"  # язык и версия подхватились
    assert body["locale"] == "kg"


async def test_unregister_removes_and_is_idempotent(
    client: AsyncClient, owner: dict[str, str]
) -> None:
    """Снятие удаляет устройство и повторный вызов не ошибка."""
    token = _token()
    created = await client.post(
        f"{API}/clinic/devices",
        headers=owner,
        json={"token": token, "platform": "android"},
    )
    first_id = created.json()["id"]

    gone = await client.delete(f"{API}/clinic/devices/{token}", headers=owner)
    assert gone.status_code == 200, gone.text
    # Второй DELETE — тоже 200: на выходе из приложения важен факт, а не код.
    again = await client.delete(f"{API}/clinic/devices/{token}", headers=owner)
    assert again.status_code == 200, again.text

    # Регистрация того же токена после снятия заводит НОВУЮ запись — значит,
    # старая действительно удалена, а не осталась висеть.
    recreated = await client.post(
        f"{API}/clinic/devices",
        headers=owner,
        json={"token": token, "platform": "android"},
    )
    assert recreated.json()["id"] != first_id


async def test_unregister_unknown_token_is_ok(client: AsyncClient, owner: dict[str, str]) -> None:
    """Снять никогда не регистрировавшийся токен — 200 (идемпотентность)."""
    resp = await client.delete(f"{API}/clinic/devices/{_token()}", headers=owner)
    assert resp.status_code == 200, resp.text


async def test_unregister_only_affects_own_device(
    admin_client: AsyncClient, client: AsyncClient, owner: dict[str, str]
) -> None:
    """Чужой токен снять нельзя: владение — по аккаунту из токена, не по факту знания токена."""
    token = _token()
    created = await client.post(
        f"{API}/clinic/devices",
        headers=owner,
        json={"token": token, "platform": "ios"},
    )
    owner_device_id = created.json()["id"]

    # Другой владелец (другая клиника) пытается снять чужой токен.
    stranger = await _make_owner(admin_client, client, "Чужой")
    attempt = await client.delete(f"{API}/clinic/devices/{token}", headers=stranger)
    assert attempt.status_code == 200, attempt.text  # идемпотентно, но это no-op

    # Устройство на месте: повторная регистрация владельцем возвращает ТУ ЖЕ строку.
    still = await client.post(
        f"{API}/clinic/devices",
        headers=owner,
        json={"token": token, "platform": "ios"},
    )
    assert still.json()["id"] == owner_device_id


async def test_register_defaults_locale_to_ru(client: AsyncClient, owner: dict[str, str]) -> None:
    """locale необязателен — по умолчанию ru."""
    resp = await client.post(
        f"{API}/clinic/devices",
        headers=owner,
        json={"token": _token(), "platform": "android"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["locale"] == "ru"
    assert resp.json()["appVersion"] is None


# ── Настройки уведомлений ────────────────────────────────────────────────────


async def test_update_prefs_sets_disabled_and_quiet_hours(
    client: AsyncClient, owner: dict[str, str]
) -> None:
    """Отключённые типы и тихие часы сохраняются и возвращаются в camelCase."""
    resp = await client.put(
        f"{API}/clinic/notifications/prefs",
        headers=owner,
        json={
            "disabled": ["day.morning", "day.evening"],
            "quietFrom": "21:00:00",
            "quietTo": "07:30:00",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["disabled"] == ["day.morning", "day.evening"]
    assert body["quietFrom"] == "21:00:00"
    assert body["quietTo"] == "07:30:00"


async def test_prefs_null_disabled_is_422(client: AsyncClient, owner: dict[str, str]) -> None:
    """`disabled` NOT NULL: явный null отвергается (пусто — это [], а не null)."""
    resp = await client.put(
        f"{API}/clinic/notifications/prefs", headers=owner, json={"disabled": None}
    )
    assert resp.status_code == 422, resp.text


async def test_prefs_null_quiet_hours_clears_them(
    client: AsyncClient, owner: dict[str, str]
) -> None:
    """null у тихих часов снимает их; невзятые поля (disabled) не трогаются."""
    await client.put(
        f"{API}/clinic/notifications/prefs",
        headers=owner,
        json={"disabled": ["day.morning"], "quietFrom": "22:00:00", "quietTo": "08:00:00"},
    )
    cleared = await client.put(
        f"{API}/clinic/notifications/prefs",
        headers=owner,
        json={"quietFrom": None, "quietTo": None},
    )
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body["quietFrom"] is None
    assert body["quietTo"] is None
    assert body["disabled"] == ["day.morning"]  # частичная правка не сбросила остальное


async def test_prefs_upsert_then_update(client: AsyncClient, owner: dict[str, str]) -> None:
    """Первая правка заводит строку, вторая — обновляет её."""
    first = await client.put(
        f"{API}/clinic/notifications/prefs", headers=owner, json={"disabled": ["debt.created"]}
    )
    assert first.status_code == 200, first.text
    assert first.json()["disabled"] == ["debt.created"]

    second = await client.put(
        f"{API}/clinic/notifications/prefs", headers=owner, json={"disabled": []}
    )
    assert second.status_code == 200, second.text
    assert second.json()["disabled"] == []


# ── Доступ ───────────────────────────────────────────────────────────────────


async def test_worker_can_manage_own_push(client: AsyncClient, owner: dict[str, str]) -> None:
    """Пуши доступны любому члену клиники — врачу тоже (спец-права не нужно)."""
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    added = await client.post(
        f"{API}/clinic/staff",
        headers=owner,
        json={"fullName": "Врач Пуш", "phone": phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    worker = await otp_login(client, phone)

    device = await client.post(
        f"{API}/clinic/devices",
        headers=worker,
        json={"token": _token(), "platform": "android"},
    )
    assert device.status_code == 200, device.text
    prefs = await client.put(
        f"{API}/clinic/notifications/prefs", headers=worker, json={"disabled": ["day.morning"]}
    )
    assert prefs.status_code == 200, prefs.text


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/clinic/devices", {"token": "x", "platform": "android"}),
        ("DELETE", "/clinic/devices/x", None),
        ("PUT", "/clinic/notifications/prefs", {"disabled": []}),
    ],
)
async def test_push_endpoints_require_auth(
    client: AsyncClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    """Все три ручки закрыты авторизацией."""
    resp = await client.request(method, f"{API}{path}", json=body)
    assert resp.status_code == 401, resp.text
