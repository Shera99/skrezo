"""API-тесты связки «услуга ↔ врачи».

Прайс раньше ничего не знал о врачах: на имплантацию записывали к терапевту, а
«ближайшее свободное на эту услугу» фронт собирал, опрашивая слоты каждого врача.

Ключевые риски:

* ``null`` и ``[]`` — РАЗНЫЕ состояния. Отсутствие ограничения (услугу делают
  все) нельзя перепутать с «ограничение есть, врачей не осталось», иначе
  увольнение последнего врача молча открывает услугу всем;
* уволенный не должен пропадать из настройки: увольнение мягкое, и вернувшемуся
  врачу не должны заново проставлять услуги;
* чужое членство в наборе — 422 со списком лишних, а не тихое игнорирование;
* подбор по услуге не должен открывать врачу с ``view_own`` чужое расписание.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070089{next(_phone_seq):04d}"


def _workday() -> str:
    day = date.today() + timedelta(days=1)
    if day.weekday() == 6:  # воскресенье — выходной по умолчанию
        day += timedelta(days=1)
    return day.isoformat()


@dataclass(slots=True)
class ClinicCtx:
    headers: dict[str, str]
    first_id: str  # врач 1
    second_id: str  # врач 2
    patient_id: str


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Услуг",
            "adminPhone": phone,
            "name": "Клиника Услуг",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(client, phone)
    return ClinicCtx(
        headers=headers,
        first_id=await _doctor(client, headers, "Врач Первый"),
        second_id=await _doctor(client, headers, "Врач Второй"),
        patient_id=await _patient(client, headers),
    )


async def _doctor(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    added = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": name, "phone": _phone(), "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    return str(added.json()["membershipId"])


async def _patient(client: AsyncClient, headers: dict[str, str]) -> str:
    resp = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Услугин"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _service(
    client: AsyncClient, ctx: ClinicCtx, name: str, doctor_ids: list[str] | None = None
) -> str:
    body: dict[str, Any] = {"name": name, "priceTiyin": 3000, "duration": 30}
    if doctor_ids is not None:
        body["doctorIds"] = doctor_ids
    resp = await client.post(f"{API}/clinic/services", headers=ctx.headers, json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# ── 1. null против [] ───────────────────────────────────────────────────────


async def test_service_without_restriction_returns_null(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Услуга без назначений — `null`: делают все, миграции старому прайсу не нужно."""
    await _service(client, ctx, "Общая")
    listing = await client.get(f"{API}/clinic/services", headers=ctx.headers)
    assert listing.json()[0]["doctorIds"] is None


async def test_assigned_doctors_come_back(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Назначенные врачи возвращаются и в ответе создания, и в списке."""
    created = await client.post(
        f"{API}/clinic/services",
        headers=ctx.headers,
        json={"name": "Имплантация", "priceTiyin": 90000, "doctorIds": [ctx.first_id]},
    )
    assert created.status_code == 201, created.text
    assert created.json()["doctorIds"] == [ctx.first_id]

    listing = await client.get(f"{API}/clinic/services", headers=ctx.headers)
    assert listing.json()[0]["doctorIds"] == [ctx.first_id]


async def test_fired_doctor_leaves_empty_list_not_null(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Уволили единственного врача услуги — `[]`, а НЕ `null`.

    Это главная ловушка: `null` означал бы «услугу снова делают все», то есть
    ровно противоположное тому, что произошло.
    """
    service_id = await _service(client, ctx, "Имплантация", [ctx.first_id])

    removed = await client.delete(f"{API}/clinic/staff/{ctx.first_id}", headers=ctx.headers)
    assert removed.status_code == 200, removed.text

    listing = await client.get(f"{API}/clinic/services", headers=ctx.headers)
    service = next(s for s in listing.json() if s["id"] == service_id)
    assert service["doctorIds"] == []


async def test_setting_is_kept_for_returning_doctor(
    client: AsyncClient, ctx: ClinicCtx, _resources: Any
) -> None:
    """Настройка переживает увольнение: связь не чистится, только фильтруется.

    Восстановить сотрудника ручкой сейчас нельзя, поэтому членство возвращаем
    прямо в БД — проверяется именно то, что запись в прайсе не потеряна.
    """
    from sqlalchemy import text

    service_id = await _service(client, ctx, "Имплантация", [ctx.first_id, ctx.second_id])
    await client.delete(f"{API}/clinic/staff/{ctx.first_id}", headers=ctx.headers)

    listing = await client.get(f"{API}/clinic/services", headers=ctx.headers)
    assert next(s for s in listing.json() if s["id"] == service_id)["doctorIds"] == [
        ctx.second_id
    ]

    async with _resources.factory() as session:
        await session.execute(
            text("UPDATE membership SET active = true WHERE id = :id"), {"id": ctx.first_id}
        )
        await session.commit()

    listing = await client.get(f"{API}/clinic/services", headers=ctx.headers)
    restored = next(s for s in listing.json() if s["id"] == service_id)["doctorIds"]
    assert set(restored) == {ctx.first_id, ctx.second_id}


# ── 2. Валидация набора ─────────────────────────────────────────────────────


async def test_foreign_membership_is_rejected(
    admin_client: AsyncClient, client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Врач чужой клиники в наборе — 422 со списком лишних, а не тихое игнорирование."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    other_phone = _phone()
    await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Чужой",
            "adminPhone": other_phone,
            "name": "Чужая клиника",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    other_headers = await otp_login(client, other_phone)
    outsider = await _doctor(client, other_headers, "Чужой Врач")

    resp = await client.post(
        f"{API}/clinic/services",
        headers=ctx.headers,
        json={"name": "Имплантация", "priceTiyin": 90000, "doctorIds": [outsider]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "unknown_doctors"
    assert outsider in resp.json()["message"]

    # Услуга не создалась — полуфабриката в прайсе не осталось.
    listing = await client.get(f"{API}/clinic/services", headers=ctx.headers)
    assert listing.json() == []


async def test_empty_list_on_write_is_rejected(client: AsyncClient, ctx: ClinicCtx) -> None:
    """`[]` на запись — 422: иначе на чтении это вернулось бы как «делают все»."""
    resp = await client.post(
        f"{API}/clinic/services",
        headers=ctx.headers,
        json={"name": "Никакая", "priceTiyin": 1000, "doctorIds": []},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "empty_doctor_ids"


async def test_null_removes_restriction(client: AsyncClient, ctx: ClinicCtx) -> None:
    """`null` в PATCH снимает ограничение; не передать поле — не трогать его."""
    service_id = await _service(client, ctx, "Имплантация", [ctx.first_id])

    renamed = await client.patch(
        f"{API}/clinic/services/{service_id}", headers=ctx.headers, json={"name": "Импланты"}
    )
    assert renamed.json()["doctorIds"] == [ctx.first_id]  # набор не тронут

    opened = await client.patch(
        f"{API}/clinic/services/{service_id}", headers=ctx.headers, json={"doctorIds": None}
    )
    assert opened.status_code == 200, opened.text
    assert opened.json()["doctorIds"] is None


# ── 3. Запись на приём ──────────────────────────────────────────────────────


async def test_booking_to_wrong_doctor_is_rejected(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Врач услугу не выполняет — 422; тот, кто выполняет, записывается."""
    service_id = await _service(client, ctx, "Имплантация", [ctx.first_id])
    slot = f"{_workday()}T10:00:00+06:00"

    wrong = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "doctorMembershipId": ctx.second_id,
            "serviceId": service_id,
            "scheduledAt": slot,
        },
    )
    assert wrong.status_code == 422, wrong.text
    assert wrong.json()["error"] == "doctor_cant_do_service"

    right = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "doctorMembershipId": ctx.first_id,
            "serviceId": service_id,
            "scheduledAt": slot,
        },
    )
    assert right.status_code == 201, right.text


async def test_force_overrides_service_restriction(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Администратор клиники может записать «всё равно» — тем же force."""
    service_id = await _service(client, ctx, "Имплантация", [ctx.first_id])
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "doctorMembershipId": ctx.second_id,
            "serviceId": service_id,
            "scheduledAt": f"{_workday()}T11:00:00+06:00",
            "force": True,
        },
    )
    assert resp.status_code == 201, resp.text


async def test_service_without_restriction_books_to_anyone(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Услуга без назначений записывается к любому врачу — старый прайс не сломан."""
    service_id = await _service(client, ctx, "Осмотр")
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "doctorMembershipId": ctx.second_id,
            "serviceId": service_id,
            "scheduledAt": f"{_workday()}T14:00:00+06:00",  # мимо обеда 12:00–13:00
        },
    )
    assert resp.status_code == 201, resp.text


async def test_changing_doctor_rechecks_service(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Смена врача у существующего приёма проверяется так же, как создание."""
    service_id = await _service(client, ctx, "Имплантация", [ctx.first_id])
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "doctorMembershipId": ctx.first_id,
            "serviceId": service_id,
            "scheduledAt": f"{_workday()}T13:00:00+06:00",
        },
    )
    assert booked.status_code == 201, booked.text

    moved = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=ctx.headers,
        json={"doctorMembershipId": ctx.second_id},
    )
    assert moved.status_code == 422, moved.text
    assert moved.json()["error"] == "doctor_cant_do_service"


# ── 4. Слоты по услуге ──────────────────────────────────────────────────────


async def test_slots_by_service_cover_only_its_doctors(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Без doctorId слоты собираются по врачам услуги, и у каждого свой врач."""
    service_id = await _service(client, ctx, "Имплантация", [ctx.first_id])
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={_workday()}&serviceId={service_id}",
        headers=ctx.headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["doctorId"] is None  # спрашивали не про конкретного
    assert body["slots"], "у врача должны быть свободные слоты"
    assert {s["doctorId"] for s in body["slots"]} == {ctx.first_id}
    # По возрастанию времени — экран показывает «ближайшее свободное».
    starts = [s["startAt"] for s in body["slots"]]
    assert starts == sorted(starts)


async def test_slots_by_open_service_cover_all_doctors(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Услуга без назначений — в подбор идут все практикующие клиники."""
    service_id = await _service(client, ctx, "Осмотр")
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={_workday()}&serviceId={service_id}",
        headers=ctx.headers,
    )
    assert resp.status_code == 200, resp.text
    assert {s["doctorId"] for s in resp.json()["slots"]} == {ctx.first_id, ctx.second_id}


async def test_slots_without_doctor_and_service_is_422(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Ни врача, ни услуги — подбирать не по чему."""
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={_workday()}", headers=ctx.headers
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "doctor_or_service_required"


async def test_slots_by_doctor_still_work(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Прежний вызов по врачу не сломан, и врач продублирован в каждом слоте."""
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={_workday()}&doctorId={ctx.first_id}",
        headers=ctx.headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["doctorId"] == ctx.first_id
    assert {s["doctorId"] for s in resp.json()["slots"]} == {ctx.first_id}


async def test_view_own_sees_only_himself_in_service_slots(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Подбор по услуге не обходит `view_own`: врач видит только своё время."""
    service_id = await _service(client, ctx, "Осмотр")  # делают все
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    added = await client.post(
        f"{API}/clinic/staff",
        headers=ctx.headers,
        json={"fullName": "Работник Свой", "phone": phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    worker_id = str(added.json()["membershipId"])
    worker = await otp_login(client, phone)

    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={_workday()}&serviceId={service_id}",
        headers=worker,
    )
    assert resp.status_code == 200, resp.text
    assert {s["doctorId"] for s in resp.json()["slots"]} == {worker_id}
