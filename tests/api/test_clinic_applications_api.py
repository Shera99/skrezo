"""API-тесты заявок клиник (второй путь регистрации).

Подача возможна только после подтверждённого OTP: телефон берётся из билета, а не
из тела. Рассмотрение (список/карточка/статусы) — только для команды Skrezo.

Ключевые риски:

* заявку нельзя подать без подтверждения телефона (и на чужой номер);
* билет одноразовый, но ошибка валидации его не сжигает;
* админ-контур закрыт от сотрудников клиник и без токена;
* статусы меняются и фиксируют, кто их поставил.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.shared.domain.value_objects import normalize_phone
from tests.conftest import API, Resources

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070099{next(_phone_seq):04d}"


async def _ticket(client: AsyncClient, res: Resources, phone: str) -> str:
    """Пройти шаги 1–2 (запрос кода + подтверждение) и получить билет."""
    requested = await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text
    code: str | None = await res.fake.get(f"auth:otp:code:login:{normalize_phone(phone)}")
    assert code is not None
    verify = await client.post(f"{API}/auth/otp/verify", json={"phone": phone, "code": code})
    assert verify.status_code == 200, verify.text
    token: str = verify.json()["registrationToken"]
    return token


async def _city(client: AsyncClient) -> str:
    city: str = (await client.get(f"{API}/cities")).json()[0]["id"]
    return city


async def _submit(
    client: AsyncClient,
    res: Resources,
    *,
    name: str = "Дента+",
    doctors: str = "6-10",
) -> dict[str, Any]:
    """Подать заявку целиком (OTP → форма). Вернуть тело ответа."""
    phone = _phone()
    token = await _ticket(client, res, phone)
    resp = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": token,
            "companyName": name,
            "cityId": await _city(client),
            "doctorsCount": doctors,
        },
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    body["_phone"] = normalize_phone(phone)
    return body


# ── 1. Подача заявки ────────────────────────────────────────────────────────


async def test_submit_application_after_otp(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Заявка подаётся после OTP и попадает в админку со статусом pending."""
    submitted = await _submit(client, _resources, name="Клиника Мечты", doctors="10+")
    assert submitted["status"] == "pending"

    listed = await admin_client.get(f"{API}/admin/clinic-applications")
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json()["list"] if x["id"] == submitted["id"])
    assert row["companyName"] == "Клиника Мечты"
    assert row["doctorsCount"] == "10+"
    assert row["phone"] == submitted["_phone"]  # телефон из OTP, не из тела
    assert row["cityName"]  # город локализован
    assert row["status"] == "pending"
    assert listed.json()["statusCounts"]["pending"] >= 1


async def test_submit_without_ticket_is_400(client: AsyncClient) -> None:
    """Без подтверждённого телефона заявка не принимается."""
    resp = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": "bogus",
            "companyName": "Ничья",
            "cityId": await _city(client),
            "doctorsCount": "2-5",
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "registration_not_verified"


async def test_ticket_is_single_use(client: AsyncClient, _resources: Resources) -> None:
    """Билет одноразовый: вторая заявка тем же токеном → 400."""
    phone = _phone()
    token = await _ticket(client, _resources, phone)
    payload = {
        "registrationToken": token,
        "companyName": "Дубль",
        "cityId": await _city(client),
        "doctorsCount": "2-5",
    }
    first = await client.post(f"{API}/clinic-applications", json=payload)
    assert first.status_code == 201, first.text
    second = await client.post(f"{API}/clinic-applications", json=payload)
    assert second.status_code == 400, second.text


async def test_unknown_city_is_422_and_ticket_survives(
    client: AsyncClient, _resources: Resources
) -> None:
    """Неизвестный город → 422, билет не сгорает (можно исправить и повторить)."""
    phone = _phone()
    token = await _ticket(client, _resources, phone)
    bad = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": token,
            "companyName": "Город?",
            "cityId": "00000000-0000-4000-8000-000000000000",
            "doctorsCount": "2-5",
        },
    )
    assert bad.status_code == 422, bad.text
    assert bad.json()["error"] == "city_not_found"

    good = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": token,
            "companyName": "Город!",
            "cityId": await _city(client),
            "doctorsCount": "2-5",
        },
    )
    assert good.status_code == 201, good.text


async def test_invalid_doctors_count_is_422(
    client: AsyncClient, _resources: Resources
) -> None:
    """Размер клиники вне списка (2-5 / 6-10 / 10+) → 422."""
    phone = _phone()
    token = await _ticket(client, _resources, phone)
    resp = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": token,
            "companyName": "Много",
            "cityId": await _city(client),
            "doctorsCount": "100500",
        },
    )
    assert resp.status_code == 422, resp.text


# ── 2. Рассмотрение в админке ───────────────────────────────────────────────


async def test_admin_reads_and_changes_status(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Карточка читается; статус меняется и фиксирует, кто его поставил."""
    submitted = await _submit(client, _resources, name="Статусная")
    application_id = submitted["id"]

    card = await admin_client.get(f"{API}/admin/clinic-applications/{application_id}")
    assert card.status_code == 200, card.text
    assert card.json()["reviewedAt"] is None

    reviewed = await admin_client.patch(
        f"{API}/admin/clinic-applications/{application_id}/status",
        json={"status": "reviewed"},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "reviewed"
    assert reviewed.json()["reviewedAt"] is not None
    assert reviewed.json()["reviewedByName"]  # кто из команды Skrezo

    # Решение можно пересмотреть — переходы свободные.
    rejected = await admin_client.patch(
        f"{API}/admin/clinic-applications/{application_id}/status",
        json={"status": "rejected"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"


async def test_admin_filters_by_status(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Фильтр по статусу отдаёт только заявки в этом статусе."""
    pending = await _submit(client, _resources, name="Ждёт")
    other = await _submit(client, _resources, name="Отклонена")
    await admin_client.patch(
        f"{API}/admin/clinic-applications/{other['id']}/status", json={"status": "rejected"}
    )

    only_rejected = await admin_client.get(f"{API}/admin/clinic-applications?status=rejected")
    ids = {x["id"] for x in only_rejected.json()["list"]}
    assert other["id"] in ids
    assert pending["id"] not in ids


async def test_admin_unknown_application_is_404(admin_client: AsyncClient) -> None:
    """Карточка и смена статуса несуществующей заявки → 404."""
    missing = uuid.uuid4()
    card = await admin_client.get(f"{API}/admin/clinic-applications/{missing}")
    assert card.status_code == 404, card.text
    assert card.json()["error"] == "application_not_found"

    patched = await admin_client.patch(
        f"{API}/admin/clinic-applications/{missing}/status", json={"status": "reviewed"}
    )
    assert patched.status_code == 404, patched.text


async def test_clinic_staff_cannot_read_applications(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Сотрудник клиники не имеет доступа к заявкам (нужен platform_admin)."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": phone,
            "name": "Не для заявок",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    login = await client.post(
        f"{API}/auth/login", json={"phone": phone, "password": created.json()["tempPassword"]}
    )
    headers = {"Authorization": f"Bearer {login.json()['accessToken']}"}

    resp = await client.get(f"{API}/admin/clinic-applications", headers=headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "not_platform_admin"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/admin/clinic-applications"),
        ("GET", f"/admin/clinic-applications/{uuid.uuid4()}"),
        ("PATCH", f"/admin/clinic-applications/{uuid.uuid4()}/status"),
    ],
)
async def test_admin_endpoints_without_token_401(
    client: AsyncClient, method: str, path: str
) -> None:
    """Админ-ручки заявок закрыты авторизацией."""
    resp = await client.request(method, f"{API}{path}", json={"status": "reviewed"})
    assert resp.status_code == 401, resp.text
