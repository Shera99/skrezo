"""API-тесты дырок кабинета клиники, найденных фронтом.

Каждый тест закрывает конкретный костыль на стороне клиента:

* тип кабинета в токене — соло отличали по числу сотрудников, и шаг «Команда»
  в онбординге показывался всем (у новой клиники сотрудников тоже ноль);
* сброс индивидуального графика — вернуть врача «как у клиники» было нечем,
  копия часов клиники за её изменением не едет;
* места в подписке — лимит по тарифу есть, а «сколько ещё можно добавить» негде
  взять;
* отсутствия в ленте дня — их тянули отдельным запросом на каждого врача,
  хотя ручка дня уже грузит их для себя.
"""

from __future__ import annotations

import itertools
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, otp_login, own_membership_id

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070099{next(_phone_seq):04d}"


async def _clinic(admin_client: AsyncClient, client: AsyncClient) -> dict[str, str]:
    """Клиника с владельцем; вернуть заголовки владельца."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Кабинета",
            "adminPhone": phone,
            "name": f"Клиника Кабинета {phone}",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    return await otp_login(client, phone)


async def _solo(admin_client: AsyncClient, client: AsyncClient) -> dict[str, str]:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "solo",
            "adminFullName": "Соло Врач",
            "adminPhone": phone,
            "cityId": city,
        },
    )
    assert created.status_code == 201, created.text
    return await otp_login(client, phone)


# ── 1. Тип кабинета в токене ────────────────────────────────────────────────


@pytest.mark.parametrize("clinic_type", ["clinic", "solo"])
async def test_me_exposes_clinic_type(
    admin_client: AsyncClient, client: AsyncClient, clinic_type: str
) -> None:
    """Соло отличается от клиники полем, а не числом сотрудников.

    У только что заведённой клиники сотрудников тоже ноль, поэтому счётом их
    не развести — а от этого зависит, показывать ли шаг «Команда».
    """
    headers = (
        await _solo(admin_client, client)
        if clinic_type == "solo"
        else await _clinic(admin_client, client)
    )
    me = await client.get(f"{API}/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["activeClinic"]["clinicType"] == clinic_type
    assert me.json()["clinics"][0]["clinicType"] == clinic_type


async def test_clinic_type_comes_with_login(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Тип приходит уже на входе — до первого запроса /auth/me."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Соло Вход", "adminPhone": phone, "cityId": city},
    )
    headers = await otp_login(client, phone)
    assert headers  # вход прошёл

    me = await client.get(f"{API}/auth/me", headers=headers)
    assert me.json()["activeClinic"]["clinicType"] == "solo"


# ── 2. Сброс индивидуального графика ────────────────────────────────────────


_FULL_WEEK: list[dict[str, Any]] = [
    {"weekday": d, "startsAt": "10:00:00", "endsAt": "14:00:00", "isWorking": True}
    for d in range(7)
]


async def test_doctor_hours_can_be_reset_to_clinic(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """DELETE возвращает врача на часы клиники, а не на копию прежних."""
    headers = await _clinic(admin_client, client)
    doctor_id = await own_membership_id(client, headers)
    base = f"{API}/clinic/schedule/doctors/{doctor_id}/hours"

    saved = await client.put(base, headers=headers, json={"days": _FULL_WEEK})
    assert saved.status_code == 200, saved.text
    assert saved.json()["days"][0]["endsAt"] == "14:00:00"

    removed = await client.delete(base, headers=headers)
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"ok": True, "had": True}

    # График снят: чтение показывает часы клиники (ручка их подставляет, когда
    # своего графика нет), а не прежние 10:00–14:00.
    restored = await client.get(base, headers=headers)
    assert restored.json()["days"][0]["startsAt"] == "09:00:00"
    assert restored.json()["days"][0]["endsAt"] == "18:00:00"

    # И окно в ленте дня совпало с часами клиники.
    day = await client.get(
        f"{API}/clinic/schedule/day?date={_next_workday()}", headers=headers
    )
    column = next(c for c in day.json()["doctors"] if c["membershipId"] == doctor_id)
    assert column["worksFrom"] == day.json()["opensAt"]
    assert column["worksTo"] == day.json()["closesAt"]


async def test_reset_without_hours_is_not_an_error(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Повторный сброс — тот же результат, о котором просили: had=false, 200."""
    headers = await _clinic(admin_client, client)
    doctor_id = await own_membership_id(client, headers)
    resp = await client.delete(
        f"{API}/clinic/schedule/doctors/{doctor_id}/hours", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["had"] is False


async def test_reset_of_unknown_doctor_is_404(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    headers = await _clinic(admin_client, client)
    resp = await client.delete(
        f"{API}/clinic/schedule/doctors/00000000-0000-4000-8000-000000000000/hours",
        headers=headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "doctor_not_found"


# ── 3. Места в подписке ─────────────────────────────────────────────────────


async def test_subscription_shows_seats(admin_client: AsyncClient, client: AsyncClient) -> None:
    """«Сколько ещё можно добавить» считается по этим двум числам."""
    headers = await _clinic(admin_client, client)

    empty = await client.get(f"{API}/clinic/subscription", headers=headers)
    assert empty.status_code == 200, empty.text
    assert empty.json()["seatsLimit"] == 5
    assert empty.json()["seatsUsed"] == 0  # владелец места не занимает

    await own_membership_id(client, headers)  # заводит одного врача
    after = await client.get(f"{API}/clinic/subscription", headers=headers)
    assert after.json()["seatsUsed"] == 1


async def test_solo_subscription_has_no_seat_limit(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """У соло сотрудников не бывает — лимита нет, а не «ноль мест»."""
    headers = await _solo(admin_client, client)
    resp = await client.get(f"{API}/clinic/subscription", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["seatsLimit"] is None
    assert resp.json()["seatsUsed"] == 0


# ── 4. Отсутствия в ленте дня ───────────────────────────────────────────────


def _next_workday() -> str:
    """Ближайший будний день — воскресенье в клинике выходной по умолчанию."""
    day = date.today() + timedelta(days=1)
    if day.weekday() == 6:
        day += timedelta(days=1)
    return day.isoformat()


async def test_day_view_includes_partial_time_off(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Частичное отсутствие видно в ленте дня — без запроса time-off на врача.

    Врач, уехавший на два часа, остаётся рабочим: `isWorking` про это молчит, и
    дыру на сетке показать было нечем.
    """
    headers = await _clinic(admin_client, client)
    doctor_id = await own_membership_id(client, headers)
    day = _next_workday()

    created = await client.post(
        f"{API}/clinic/schedule/doctors/{doctor_id}/time-off",
        headers=headers,
        json={
            "startsAt": f"{day}T11:00:00+06:00",
            "endsAt": f"{day}T13:00:00+06:00",
            "reason": "Учёба",
        },
    )
    assert created.status_code == 201, created.text

    resp = await client.get(f"{API}/clinic/schedule/day?date={day}", headers=headers)
    assert resp.status_code == 200, resp.text
    column = next(c for c in resp.json()["doctors"] if c["membershipId"] == doctor_id)
    assert column["isWorking"] is True  # уехал не на весь день
    assert len(column["timeOff"]) == 1
    off = column["timeOff"][0]
    # Момент абсолютный: сервер отдаёт его в UTC, клиника живёт в +06:00.
    assert datetime.fromisoformat(off["startAt"]) == datetime.fromisoformat(
        f"{day}T11:00:00+06:00"
    )
    assert datetime.fromisoformat(off["endAt"]) == datetime.fromisoformat(
        f"{day}T13:00:00+06:00"
    )


async def test_day_view_time_off_is_empty_without_absences(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Нет отсутствий — пустой список, а не отсутствующее поле."""
    headers = await _clinic(admin_client, client)
    doctor_id = await own_membership_id(client, headers)
    resp = await client.get(
        f"{API}/clinic/schedule/day?date={_next_workday()}", headers=headers
    )
    column = next(c for c in resp.json()["doctors"] if c["membershipId"] == doctor_id)
    assert column["timeOff"] == []


async def test_full_day_absence_is_both_flag_and_span(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Отсутствие на весь день гасит isWorking и остаётся видно как интервал."""
    headers = await _clinic(admin_client, client)
    doctor_id = await own_membership_id(client, headers)
    day = _next_workday()

    created = await client.post(
        f"{API}/clinic/schedule/doctors/{doctor_id}/time-off",
        headers=headers,
        json={"startsAt": f"{day}T00:00:00+06:00", "endsAt": f"{day}T23:59:00+06:00"},
    )
    assert created.status_code == 201, created.text

    resp = await client.get(f"{API}/clinic/schedule/day?date={day}", headers=headers)
    column = next(c for c in resp.json()["doctors"] if c["membershipId"] == doctor_id)
    assert column["isWorking"] is False
    assert len(column["timeOff"]) == 1  # видно, почему именно не работает


# ── 5. Врач обязателен ──────────────────────────────────────────────────────


async def test_draft_without_doctor_cannot_be_promoted(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Черновик без врача не «доводится» до записи молча — 422 doctor_required."""
    headers = await _clinic(admin_client, client)
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Черновиков"},
    )
    draft = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={"patientId": patient.json()["id"], "status": "draft"},
    )
    assert draft.status_code == 201, draft.text

    promoted = await client.patch(
        f"{API}/clinic/appointments/{draft.json()['id']}",
        headers=headers,
        json={"status": "scheduled", "scheduledAt": f"{_next_workday()}T10:00:00+06:00"},
    )
    assert promoted.status_code == 422, promoted.text
    assert promoted.json()["error"] == "doctor_required"

    # С врачом тот же переход проходит.
    doctor_id = await own_membership_id(client, headers)
    ok = await client.patch(
        f"{API}/clinic/appointments/{draft.json()['id']}",
        headers=headers,
        json={
            "status": "scheduled",
            "scheduledAt": f"{_next_workday()}T10:00:00+06:00",
            "doctorMembershipId": doctor_id,
        },
    )
    assert ok.status_code == 200, ok.text


async def test_cancelled_draft_does_not_need_a_doctor(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Передумавшего пациента закрывают отменой — врача у него так и не было."""
    headers = await _clinic(admin_client, client)
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Передумов"},
    )
    draft = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={"patientId": patient.json()["id"], "status": "draft"},
    )
    cancelled = await client.patch(
        f"{API}/clinic/appointments/{draft.json()['id']}",
        headers=headers,
        json={"status": "cancelled", "cancelReason": "patient_request"},
    )
    assert cancelled.status_code == 200, cancelled.text
