"""Тесты сводки дня и правки своего профиля.

Сводка дня почти повторяет `GET /clinic/reports/summary` за сегодня, и отдельная
ручка оправдана ровно двумя отличиями — их и проверяем:

* **сутки считает сервер** в таймзоне клиники: фронту не нужно знать её
  таймзону, чтобы попросить правильный день (и ошибаться на границе суток);
* **долг здесь общий**, а не за день: утром важно, сколько висит всего.

Профиль: сотрудник меняет своё имя и язык, но НЕ телефон — он же логин.
"""

from __future__ import annotations

import itertools
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from httpx import AsyncClient

from tests.conftest import API

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070087{next(_phone_seq):04d}"


_hour_seq = itertools.count(9)


def _today_slot() -> str:
    """Слот на СЕГОДНЯ в рабочих часах клиники.

    Общий ``working_slot`` из conftest намеренно бронирует завтра, а сводка дня
    — про сегодня, поэтому здесь свой хелпер. Час берётся из рабочего окна и не
    повторяется, чтобы приёмы не занимали один слот.
    """
    today = datetime.now(ZoneInfo("Asia/Bishkek")).date()
    return f"{today.isoformat()}T{next(_hour_seq):02d}:00:00+06:00"


async def _clinic(admin_client: AsyncClient, client: AsyncClient) -> dict[str, Any]:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Сводки",
            "adminPhone": phone,
            "name": "Клиника Сводки",
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
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Сводкин"},
    )
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Лечение", "priceTiyin": 5000}
    )
    return {
        "headers": headers,
        "patient": patient.json()["id"],
        "service": service.json()["id"],
    }


# ── Сводка дня ──────────────────────────────────────────────────────────────


async def test_dashboard_counts_today_in_clinic_timezone(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Сегодняшний приём виден в сводке, дата — в таймзоне клиники."""
    ctx = await _clinic(admin_client, client)
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx["headers"],
        json={
            "patientId": ctx["patient"],
            "scheduledAt": _today_slot(),
            "items": [{"serviceId": ctx["service"], "teeth": [46]}],
        },
    )
    assert booked.status_code == 201, booked.text

    resp = await client.get(f"{API}/clinic/dashboard", headers=ctx["headers"])

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["timezone"] == "Asia/Bishkek"
    # Дата — «сегодня» глазами клиники, а не UTC: в Бишкеке день сменяется раньше.
    assert body["date"] == datetime.now(ZoneInfo("Asia/Bishkek")).date().isoformat()
    assert body["appointments"]["total"] == 1
    assert body["appointments"]["scheduled"] == 1


async def test_dashboard_shows_day_revenue_and_total_debt(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Выручка — за сегодня, долг — за всё время, и это разные числа."""
    ctx = await _clinic(admin_client, client)
    headers = ctx["headers"]
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": ctx["patient"],
            "scheduledAt": _today_slot(),
            "items": [{"serviceId": ctx["service"], "teeth": [46]}],
        },
    )
    appointment_id = booked.json()["id"]
    completed = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/complete",
        headers=headers,
        json={"payment": {"amountTiyin": 2000, "method": "cash"}},
    )
    assert completed.status_code == 200, completed.text

    body = (await client.get(f"{API}/clinic/dashboard", headers=headers)).json()

    assert body["revenueTiyin"] == 2000  # оплачено сегодня
    assert body["outstandingTiyin"] == 3000  # начислено 5000 − оплачено 2000


async def test_dashboard_needs_reports_permission(client: AsyncClient) -> None:
    """Без токена сводка закрыта — это данные клиники."""
    resp = await client.get(f"{API}/clinic/dashboard")
    assert resp.status_code in (401, 403), resp.text


# ── Свой профиль ────────────────────────────────────────────────────────────


async def test_profile_update_changes_name_and_lang(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Сотрудник меняет своё имя и язык сам, без администратора."""
    ctx = await _clinic(admin_client, client)

    resp = await client.patch(
        f"{API}/auth/me", headers=ctx["headers"], json={"fullName": "Новое Имя", "lang": "en"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["fullName"] == "Новое Имя"
    assert resp.json()["lang"] == "en"

    me = await client.get(f"{API}/auth/me", headers=ctx["headers"])
    assert me.json()["account"]["fullName"] == "Новое Имя"


async def test_profile_update_ignores_phone(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Телефон через профиль не меняется: он логин, и его правит владелец."""
    ctx = await _clinic(admin_client, client)
    before = (await client.get(f"{API}/auth/me", headers=ctx["headers"])).json()["account"]

    resp = await client.patch(
        f"{API}/auth/me", headers=ctx["headers"], json={"phone": "0700111222"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["phone"] == before["phone"]


async def test_profile_update_rejects_explicit_null(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Явный null в обязательном поле — 422, а не пустое имя в базе."""
    ctx = await _clinic(admin_client, client)

    resp = await client.patch(f"{API}/auth/me", headers=ctx["headers"], json={"fullName": None})

    assert resp.status_code == 422, resp.text


async def test_profile_update_needs_auth(client: AsyncClient) -> None:
    resp = await client.patch(f"{API}/auth/me", json={"fullName": "Аноним"})
    assert resp.status_code in (401, 403), resp.text
