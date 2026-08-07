"""API-тесты черновика приёма («пациент заведён, дата не выбрана»).

Черновик нужен, чтобы завести человека в клинику до того, как он определился:
список пациентов клиники строится через наличие приёма, поэтому «пустой» приём —
единственный способ показать такого пациента.

Ключевые риски:

* черновик НЕ занимает слот врача: у него пустая дата, а ``tstzrange(NULL, …)`` —
  безграничный диапазон, который иначе обнулил бы все свободные слоты;
* черновик не попадает в ленту дня;
* пациент черновика виден в списке пациентов клиники (ради этого всё и делается);
* доведение до записи требует даты, а завершить черновик нельзя;
* прежняя защита от ``scheduledAt: null`` у обычных приёмов не ослабла.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from httpx import AsyncClient

from tests.conftest import API

BISHKEK = ZoneInfo("Asia/Bishkek")
_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070093{next(_phone_seq):04d}"


def _workday() -> date:
    """Ближайший рабочий день клиники (воскресенье по умолчанию выходное)."""
    today = datetime.now(BISHKEK).date()
    return today if today.weekday() != 6 else today - timedelta(days=1)


def _slot(hour: int = 10) -> str:
    return datetime.combine(_workday(), time(hour), tzinfo=BISHKEK).isoformat()


@dataclass(slots=True)
class ClinicCtx:
    headers: dict[str, str]
    doctor_id: str
    service_id: str


async def _login(client: AsyncClient, phone: str, password: str) -> dict[str, str]:
    resp = await client.post(f"{API}/auth/login", json={"phone": phone, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


async def _clinic(admin_client: AsyncClient, client: AsyncClient, name: str) -> ClinicCtx:
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
    headers = await _login(client, phone, created.json()["tempPassword"])

    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    doctor = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": f"Врач {name}", "phone": _phone(), "specializationId": spec},
    )
    assert doctor.status_code == 201, doctor.text
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Лечение", "priceTiyin": 3000}
    )
    return ClinicCtx(
        headers=headers,
        doctor_id=str(doctor.json()["membershipId"]),
        service_id=str(service.json()["id"]),
    )


async def _patient(client: AsyncClient, ctx: ClinicCtx, name: str) -> str:
    resp = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.headers,
        json={"phone": _phone(), "firstName": name, "lastName": "Черновиков"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _draft(client: AsyncClient, ctx: ClinicCtx, patient_id: str, **extra: Any) -> str:
    body: dict[str, Any] = {"patientId": patient_id, "status": "draft", **extra}
    resp = await client.post(f"{API}/clinic/appointments", headers=ctx.headers, json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# ── 1. Заведение пациента без даты ──────────────────────────────────────────


async def test_draft_registers_patient_in_clinic(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Ради этого всё и делается: пациент черновика виден в списке клиники."""
    ctx = await _clinic(admin_client, client, "Черновик Пациенты")
    patient_id = await _patient(client, ctx, "Неопределившийся")
    appointment_id = await _draft(client, ctx, patient_id)

    listed = await client.get(f"{API}/clinic/patients", headers=ctx.headers)
    assert listed.status_code == 200, listed.text
    assert patient_id in {p["id"] for p in listed.json()["list"]}

    card = await client.get(f"{API}/clinic/appointments/{appointment_id}", headers=ctx.headers)
    assert card.status_code == 200, card.text
    assert card.json()["status"] == "draft"
    assert card.json()["scheduledAt"] is None


async def test_draft_with_date_is_rejected(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Черновик с датой — противоречие: это уже обычная запись."""
    ctx = await _clinic(admin_client, client, "Черновик Дата")
    patient_id = await _patient(client, ctx, "Спорный")
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={"patientId": patient_id, "status": "draft", "scheduledAt": _slot()},
    )
    assert resp.status_code == 422, resp.text


# ── 2. Черновик не мешает расписанию ────────────────────────────────────────


async def test_draft_does_not_block_doctor_slots(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Главный риск: черновик с врачом не должен съедать свободные слоты."""
    ctx = await _clinic(admin_client, client, "Черновик Слоты")
    day = _workday().isoformat()
    before = await client.get(
        f"{API}/clinic/schedule/slots?date={day}&doctorId={ctx.doctor_id}", headers=ctx.headers
    )
    assert before.status_code == 200, before.text
    free_before = len(before.json()["slots"])
    assert free_before > 0

    patient_id = await _patient(client, ctx, "Возможно")
    await _draft(client, ctx, patient_id, doctorMembershipId=ctx.doctor_id)

    after = await client.get(
        f"{API}/clinic/schedule/slots?date={day}&doctorId={ctx.doctor_id}", headers=ctx.headers
    )
    assert after.status_code == 200, after.text
    assert len(after.json()["slots"]) == free_before  # ни один слот не занят

    # И обычная запись к этому врачу по-прежнему создаётся.
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": patient_id,
            "doctorMembershipId": ctx.doctor_id,
            "scheduledAt": _slot(),
        },
    )
    assert booked.status_code == 201, booked.text


async def test_draft_is_absent_from_day_feed(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """В ленте дня черновиков нет — у них нет даты."""
    ctx = await _clinic(admin_client, client, "Черновик Лента")
    patient_id = await _patient(client, ctx, "Невидимый")
    draft_id = await _draft(client, ctx, patient_id, doctorMembershipId=ctx.doctor_id)

    day = await client.get(
        f"{API}/clinic/schedule/day?date={_workday().isoformat()}", headers=ctx.headers
    )
    assert day.status_code == 200, day.text
    assert draft_id not in {a["id"] for a in day.json()["appointments"]}


# ── 3. Доведение до полноценной записи ──────────────────────────────────────


async def test_draft_promoted_to_real_appointment(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Черновик доводится до записи: дата + статус, слот после этого занят."""
    ctx = await _clinic(admin_client, client, "Черновик Доведение")
    patient_id = await _patient(client, ctx, "Определившийся")
    draft_id = await _draft(client, ctx, patient_id)

    promoted = await client.patch(
        f"{API}/clinic/appointments/{draft_id}",
        headers=ctx.headers,
        json={
            "status": "scheduled",
            "scheduledAt": _slot(11),
            "doctorMembershipId": ctx.doctor_id,
            "serviceId": ctx.service_id,
        },
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "scheduled"
    assert promoted.json()["scheduledAt"] is not None

    # Теперь приём виден в ленте дня.
    day = await client.get(
        f"{API}/clinic/schedule/day?date={_workday().isoformat()}", headers=ctx.headers
    )
    assert draft_id in {a["id"] for a in day.json()["appointments"]}


async def test_promoting_without_date_is_422(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Выход из черновика без даты — понятная ошибка, а не нарушение CHECK в БД."""
    ctx = await _clinic(admin_client, client, "Черновик Без Даты")
    patient_id = await _patient(client, ctx, "Забывчивый")
    draft_id = await _draft(client, ctx, patient_id)

    resp = await client.patch(
        f"{API}/clinic/appointments/{draft_id}",
        headers=ctx.headers,
        json={"status": "scheduled"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "scheduled_at_required"


async def test_promoting_into_taken_slot_is_409(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Доведение черновика проходит проверку занятости, как снятие отмены."""
    ctx = await _clinic(admin_client, client, "Черновик Занято")
    first = await _patient(client, ctx, "Первый")
    second = await _patient(client, ctx, "Второй")
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": first,
            "doctorMembershipId": ctx.doctor_id,
            "scheduledAt": _slot(14),
            "durationMin": 30,
        },
    )
    assert booked.status_code == 201, booked.text

    draft_id = await _draft(client, ctx, second)
    resp = await client.patch(
        f"{API}/clinic/appointments/{draft_id}",
        headers=ctx.headers,
        json={
            "status": "scheduled",
            "scheduledAt": _slot(14),
            "doctorMembershipId": ctx.doctor_id,
            "durationMin": 30,
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "slot_taken"


async def test_create_without_date_is_rejected(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Обычная запись без даты — 422, а не приём «на сейчас».

    Регресс прод-инцидента: фронт слал `startAt` вместо `scheduledAt`, поле
    молча игнорировалось, дефолт ставил текущий момент — и приёмы заводились
    один за другим «на сейчас», пока повторный запрос не упёрся в 409
    «слот занят». Ошибка про чужую подсистему вместо «нет даты».
    """
    ctx = await _clinic(admin_client, client, "Черновик Без даты")
    patient_id = await _patient(client, ctx, "Безымянный")
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": patient_id,
            "startAt": "2026-08-10T21:30:00+06:00",  # неизвестное поле — игнорируется
            "durationMin": 30,
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "scheduled_at_required"


async def test_draft_is_the_only_appointment_without_date(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Исключение ровно одно: черновик заводится без даты и слот не занимает."""
    ctx = await _clinic(admin_client, client, "Черновик Исключение")
    patient_id = await _patient(client, ctx, "Черновиков")
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={"patientId": patient_id, "status": "draft"},
    )
    assert resp.status_code == 201, resp.text
    card = await client.get(
        f"{API}/clinic/appointments/{resp.json()['id']}", headers=ctx.headers
    )
    assert card.json()["scheduledAt"] is None
    assert card.json()["status"] == "draft"


async def test_draft_cannot_be_completed(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Завершить и оплатить черновик нельзя — сначала дата."""
    ctx = await _clinic(admin_client, client, "Черновик Завершение")
    patient_id = await _patient(client, ctx, "Ранний")
    draft_id = await _draft(client, ctx, patient_id)

    resp = await client.post(
        f"{API}/clinic/appointments/{draft_id}/complete", headers=ctx.headers
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "cannot_complete_draft"


# ── 4. Прежние гарантии не ослабли ──────────────────────────────────────────


async def test_null_date_still_rejected_for_real_appointment(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """`scheduledAt: null` у обычного приёма по-прежнему 422 (защита из 0020)."""
    ctx = await _clinic(admin_client, client, "Черновик Null")
    patient_id = await _patient(client, ctx, "Обычный")
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={"patientId": patient_id, "scheduledAt": _slot(15)},
    )
    assert booked.status_code == 201, booked.text

    resp = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=ctx.headers,
        json={"scheduledAt": None},
    )
    assert resp.status_code == 422, resp.text


async def test_drafts_filterable_as_worklist(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Черновики достаются фильтром status=draft — список «кому перезвонить»."""
    ctx = await _clinic(admin_client, client, "Черновик Список")
    undecided = await _patient(client, ctx, "Думает")
    draft_id = await _draft(client, ctx, undecided)
    decided = await _patient(client, ctx, "Записан")
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={"patientId": decided, "scheduledAt": _slot(16)},
    )
    assert booked.status_code == 201, booked.text

    only_drafts = await client.get(
        f"{API}/clinic/appointments?status=draft", headers=ctx.headers
    )
    assert only_drafts.status_code == 200, only_drafts.text
    ids = {a["id"] for a in only_drafts.json()["list"]}
    assert draft_id in ids
    assert booked.json()["id"] not in ids
