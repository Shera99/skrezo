"""API-тесты ядра Ф3: outbox → диспетчер → инбокс (одно событие → один инбокс).

Проверяется транспортная дисциплина ТЗ-СУ:

* событие пишется в одной транзакции с изменением и доставляется диспетчером;
* адресаты — по каталогу: врач получает только СВОЁ, владелец/менеджер — всё
  своё по ролям, платежи врачу не идут;
* приоритет — от горизонта до приёма, отмена «+1» ступень;
* повторная обработка идемпотентна (unique (event_id, account_id));
* инбокс: лента своя, прочитанность на сервере, чужая запись = 404;
* черновик события не порождает; незнакомый тип не валит очередь.

Воркер в тестах не крутится — диспетчер зовётся напрямую с тестовой сессией
(тот же код, что в ``app/jobs/dispatcher_loop.py``).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.modules.push.application.event_dispatcher import EventDispatcher
from app.shared.db.models.event import Event
from app.shared.push.factory import build_push_transport
from app.shared.realtime import NoopRealtimeGateway
from tests.conftest import API, Resources, otp_login, own_membership_id

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070078{next(_phone_seq):04d}"


def _at(hours: float) -> str:
    """Момент «через N часов» (ISO, UTC) — горизонт задаётся точно, не слотами."""
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


@dataclass(slots=True)
class Ctx:
    clinic_id: str
    owner_headers: dict[str, str]
    doctor_id: str
    doctor_headers: dict[str, str]
    patient_id: str


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> Ctx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Инбокс",
            "adminPhone": phone,
            "name": "Клиника Инбокс",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(client, phone)
    doctor_id = await own_membership_id(client, headers)
    staff = await client.get(f"{API}/clinic/staff", headers=headers)
    doctor_phone = next(m["phone"] for m in staff.json() if m["specializationId"])
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Аружан", "lastName": "Инбоксова"},
    )
    assert patient.status_code == 201, patient.text
    return Ctx(
        clinic_id=str(created.json()["id"]),
        owner_headers=headers,
        doctor_id=doctor_id,
        doctor_headers=await otp_login(client, doctor_phone),
        patient_id=str(patient.json()["id"]),
    )


async def _dispatch(resources: Resources) -> int:
    """Прогнать диспетчер как это делает воркер (транспорт — лог-фолбэк)."""
    async with resources.factory() as session:
        dispatcher = EventDispatcher(session, build_push_transport(), NoopRealtimeGateway())
        return await dispatcher.dispatch_pending()


async def _book(
    client: AsyncClient, ctx: Ctx, *, hours: float, items: list[dict[str, Any]] | None = None
) -> str:
    """Запись через N часов; force снимает график — горизонт важнее рабочих окон."""
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.owner_headers,
        json={
            "patientId": ctx.patient_id,
            "doctorMembershipId": ctx.doctor_id,
            "scheduledAt": _at(hours),
            "items": items or [],
            "force": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _inbox(client: AsyncClient, headers: dict[str, str], **params: Any) -> Any:
    resp = await client.get(f"{API}/clinic/inbox", headers=headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1. Адресация и горизонт ─────────────────────────────────────────────────


async def test_created_reaches_doctor_and_owner(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Запись на послезавтрашний горизонт: инбокс врача и владельца, normal."""
    await _book(client, ctx, hours=30)
    assert await _dispatch(_resources) == 1

    doctor_box = await _inbox(client, ctx.doctor_headers)
    assert doctor_box["unreadCount"] == 1
    item = doctor_box["items"][0]
    assert item["type"] == "appointment.created"
    assert item["priority"] == "normal"  # 4–48 ч до приёма
    assert "Инбоксова" in item["body"]
    assert item["entityType"] == "appointment"

    owner_box = await _inbox(client, ctx.owner_headers)
    assert owner_box["unreadCount"] == 1
    assert owner_box["items"][0]["type"] == "appointment.created"


async def test_horizon_under_hour_is_critical(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Запись за полчаса до приёма — critical (пуш ушёл бы немедленно)."""
    await _book(client, ctx, hours=0.5)
    await _dispatch(_resources)
    item = (await _inbox(client, ctx.doctor_headers))["items"][0]
    assert item["priority"] == "critical"


async def test_cancel_bumps_one_rung(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Отмена — ступень вверх: за 30 ч это normal, за 2 ч — уже critical."""
    far = await _book(client, ctx, hours=30)
    near = await _book(client, ctx, hours=2)
    await _dispatch(_resources)

    for appointment_id, expected in ((far, "normal"), (near, "critical")):
        resp = await client.patch(
            f"{API}/clinic/appointments/{appointment_id}",
            headers=ctx.owner_headers,
            json={"status": "cancelled", "cancelReason": "patient_request"},
        )
        assert resp.status_code == 200, resp.text
        await _dispatch(_resources)
        latest = (await _inbox(client, ctx.doctor_headers))["items"][0]
        assert latest["type"] == "appointment.canceled"
        assert latest["priority"] == expected


async def test_foreign_doctor_not_notified(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """«Свои» — фильтр на сервере: чужой приём в инбокс врача не попадает."""
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    other_phone = _phone()
    other = await client.post(
        f"{API}/clinic/staff",
        headers=ctx.owner_headers,
        json={
            "fullName": "Второй Врач",
            "phone": other_phone,
            "specializationId": spec,
        },
    )
    assert other.status_code == 201, other.text
    await _book(client, ctx, hours=30)  # приём у ПЕРВОГО врача
    await _dispatch(_resources)
    other_headers = await otp_login(client, other_phone)
    assert (await _inbox(client, other_headers))["unreadCount"] == 0


async def test_payment_goes_to_owner_not_doctor(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Платёж — событие владельца/менеджера; врач видит только смену статуса."""
    appointment_id = await _book(
        client, ctx, hours=30, items=[{"priceTiyin": 5000, "quantity": 1}]
    )
    done = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/complete",
        headers=ctx.owner_headers,
        json={"payment": {"amountTiyin": 5000, "method": "cash"}},
    )
    assert done.status_code == 200, done.text
    await _dispatch(_resources)

    owner_types = {i["type"] for i in (await _inbox(client, ctx.owner_headers))["items"]}
    assert "payment.created" in owner_types
    payment_item = next(
        i
        for i in (await _inbox(client, ctx.owner_headers))["items"]
        if i["type"] == "payment.created"
    )
    assert "50" in payment_item["body"]  # 5000 тыйинов = 50 сом

    doctor_types = {i["type"] for i in (await _inbox(client, ctx.doctor_headers))["items"]}
    assert "payment.created" not in doctor_types
    assert "appointment.status_changed" in doctor_types


async def test_draft_produces_no_event(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Черновик без даты — не событие: уведомлять не о чем."""
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.owner_headers,
        json={"patientId": ctx.patient_id, "status": "draft"},
    )
    assert resp.status_code == 201, resp.text
    assert await _dispatch(_resources) == 0


# ── 2. Идемпотентность и отказоустойчивость ─────────────────────────────────


async def test_reprocessing_is_idempotent(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Повторная обработка события никому ничего не дублирует."""
    await _book(client, ctx, hours=30)
    await _dispatch(_resources)
    before = (await _inbox(client, ctx.doctor_headers))["unreadCount"]

    async with _resources.factory() as session:  # событие «забыло», что обработано
        await session.execute(update(Event).values(processed_at=None))
        await session.commit()
    assert await _dispatch(_resources) == 1
    assert (await _inbox(client, ctx.doctor_headers))["unreadCount"] == before


async def test_unknown_type_does_not_block_queue(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Незнакомый тип (ai.* до Ф2) помечается обработанным, доставки нет."""
    async with _resources.factory() as session:
        session.add(Event(clinic_id=ctx.clinic_id, type="ai.escalated"))
        await session.commit()
    assert await _dispatch(_resources) == 1
    async with _resources.factory() as session:
        event = (
            await session.execute(select(Event).where(Event.type == "ai.escalated"))
        ).scalar_one()
        assert event.processed_at is not None
    assert (await _inbox(client, ctx.owner_headers))["unreadCount"] == 0


# ── 3. API инбокса ──────────────────────────────────────────────────────────


async def test_read_flow_and_isolation(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Прочитанность на сервере; чужая запись — 404, а не чужой бейдж."""
    await _book(client, ctx, hours=30)
    await _book(client, ctx, hours=31)
    await _dispatch(_resources)

    box = await _inbox(client, ctx.doctor_headers)
    assert box["unreadCount"] == 2
    first_id = box["items"][0]["id"]

    read = await client.post(
        f"{API}/clinic/inbox/{first_id}/read", headers=ctx.doctor_headers
    )
    assert read.status_code == 200, read.text
    assert read.json()["unreadCount"] == 1

    # Врач не может отметить запись владельца (то же событие, другой снимок).
    owner_item = (await _inbox(client, ctx.owner_headers))["items"][0]["id"]
    stranger = await client.post(
        f"{API}/clinic/inbox/{owner_item}/read", headers=ctx.doctor_headers
    )
    assert stranger.status_code == 404

    unread_only = await _inbox(client, ctx.doctor_headers, unread=True)
    assert len(unread_only["items"]) == 1

    done = await client.post(f"{API}/clinic/inbox/read-all", headers=ctx.doctor_headers)
    assert done.status_code == 200
    assert done.json()["unreadCount"] == 0
    assert (await _inbox(client, ctx.doctor_headers))["unreadCount"] == 0
