"""API-тесты модуля расписания: календарь дня, слоты, часы клиники, графики врачей.

Ключевые риски, которые здесь закрываются:

* изоляция клиник (IDOR) — чужой врач/приём/график не виден и не редактируется;
* RBAC — без токена 401, без права 403;
* пересечения приёмов у одного врача — 409, но встык разрешено;
* границы суток считаются в таймзоне клиники, а не в UTC;
* нерабочее время (обед, вне часов, выходной, отсутствие врача) — 422.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError
from app.modules.appointments.application.services.booking import booking_guard
from app.shared.db.enums import AppointmentStatus
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.doctor_time_off import DoctorTimeOff
from tests.conftest import API, Resources, otp_login

# 2026-07-20 — понедельник (рабочий день по дефолту), 2026-07-19 — воскресенье (выходной).
MONDAY = "2026-07-20"
TUESDAY = "2026-07-21"
SUNDAY = "2026-07-19"

BISHKEK = ZoneInfo("Asia/Bishkek")  # UTC+6, без перехода на летнее время

# Диапазон телефонов, не пересекающийся с другими тестами: account чистится между
# тестами, а глобальный patient — нет, поэтому номера должны быть уникальны.
_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070094{next(_phone_seq):04d}"


def today() -> str:
    """Сегодняшняя дата в таймзоне клиники.

    Нужна там, где проверяется откат статуса: он разрешён только для приёма
    текущего дня, поэтому фиксированный MONDAY не подходит.
    """
    return datetime.now(BISHKEK).date().isoformat()


def at(day: str, hhmm: str, tz: ZoneInfo = BISHKEK) -> str:
    """ISO-момент «локальное время клиники» — то, что фронт кладёт в scheduledAt."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.combine(date.fromisoformat(day), time(hour, minute), tzinfo=tz).isoformat()


def hours_payload(
    *,
    opens: str = "09:00:00",
    closes: str = "18:00:00",
    break_start: str | None = "12:00:00",
    break_end: str | None = "13:00:00",
    working: tuple[int, ...] = (0, 1, 2, 3, 4, 5),
) -> dict[str, Any]:
    """Тело PUT /clinic/schedule/working-hours — всегда все 7 дней."""
    return {
        "days": [
            {
                "weekday": d,
                "opensAt": opens,
                "closesAt": closes,
                "breakStart": break_start,
                "breakEnd": break_end,
                "isWorking": d in working,
            }
            for d in range(7)
        ]
    }


def doctor_hours_payload(
    *, starts: str, ends: str, working: tuple[int, ...] = (0, 1, 2, 3, 4, 5)
) -> dict[str, Any]:
    return {
        "days": [
            {"weekday": d, "startsAt": starts, "endsAt": ends, "isWorking": d in working}
            for d in range(7)
        ]
    }


@dataclass(slots=True)
class ClinicCtx:
    """Готовая клиника: владелец, два врача, пациент и услуга на 45 минут."""

    clinic_id: str
    headers: dict[str, str]
    owner_phone: str
    doctor_id: str
    doctor2_id: str
    patient_id: str
    service45_id: str


async def _provision(
    admin_client: AsyncClient, client: AsyncClient, name: str
) -> tuple[str, dict[str, str], str]:
    """Завести клинику через админку и войти владельцем."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    resp = await admin_client.post(
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
    assert resp.status_code == 201, resp.text
    headers = await otp_login(client, phone)
    return str(resp.json()["id"]), headers, phone


async def _add_doctor(
    client: AsyncClient, headers: dict[str, str], full_name: str
) -> tuple[str, str]:
    """Добавить врача (работник + специализация) — вернуть membershipId и телефон."""
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    resp = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": full_name, "phone": phone, "specializationId": spec},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["membershipId"]), phone


async def _add_patient(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    resp = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": name, "lastName": "Тестовый"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _add_service(
    client: AsyncClient, headers: dict[str, str], name: str, duration: int | None
) -> str:
    body: dict[str, Any] = {"name": name, "priceTiyin": 1000}
    if duration is not None:
        body["duration"] = duration
    resp = await client.post(f"{API}/clinic/services", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _make_clinic(admin_client: AsyncClient, client: AsyncClient, name: str) -> ClinicCtx:
    clinic_id, headers, owner_phone = await _provision(admin_client, client, name)
    doctor_id, _ = await _add_doctor(client, headers, f"Врач 1 {name}")
    doctor2_id, _ = await _add_doctor(client, headers, f"Врач 2 {name}")
    return ClinicCtx(
        clinic_id=clinic_id,
        headers=headers,
        owner_phone=owner_phone,
        doctor_id=doctor_id,
        doctor2_id=doctor2_id,
        patient_id=await _add_patient(client, headers, f"Пациент {name}"),
        service45_id=await _add_service(client, headers, "Лечение 45", 45),
    )


@pytest.fixture
async def clinic_a(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    return await _make_clinic(admin_client, client, "Клиника A")


@pytest.fixture
async def clinic_b(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    return await _make_clinic(admin_client, client, "Клиника B")


async def _book(
    client: AsyncClient,
    ctx: ClinicCtx,
    *,
    start: str | None,
    duration: int | None = None,
    doctor: str | None | bool = True,
    service_id: str | None = None,
    force: bool = False,
    headers: dict[str, str] | None = None,
) -> Any:
    """POST /clinic/appointments со скидкой на дефолты фикстуры."""
    body: dict[str, Any] = {"patientId": ctx.patient_id}
    if force:
        body["force"] = True
    if doctor is True:
        body["doctorMembershipId"] = ctx.doctor_id
    elif isinstance(doctor, str):
        body["doctorMembershipId"] = doctor
    if start is not None:
        body["scheduledAt"] = start
    if duration is not None:
        body["durationMin"] = duration
    if service_id is not None:
        body["serviceId"] = service_id
    return await client.post(
        f"{API}/clinic/appointments", headers=headers or ctx.headers, json=body
    )


async def _set_timezone(res: Resources, clinic_id: str, tz: str) -> None:
    async with res.factory() as session:
        await session.execute(
            update(Clinic).where(Clinic.id == uuid.UUID(clinic_id)).values(timezone=tz)
        )
        await session.commit()


async def _add_time_off(
    res: Resources, clinic_id: str, membership_id: str, start: str, end: str
) -> None:
    async with res.factory() as session:
        session.add(
            DoctorTimeOff(
                clinic_id=uuid.UUID(clinic_id),
                membership_id=uuid.UUID(membership_id),
                starts_at=datetime.fromisoformat(start),
                ends_at=datetime.fromisoformat(end),
                reason="Отпуск",
            )
        )
        await session.commit()


# ── 1. Изоляция клиник (IDOR) ───────────────────────────────────────────────


async def test_day_with_doctor_id_from_other_clinic_denies_and_leaks_nothing(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Фильтр day по врачу ЧУЖОЙ клиники — 404, неотличимый от несуществующего.

    Иначе перебором id подтверждается, что такой врач где-то есть.
    """
    resp = await client.get(
        f"{API}/clinic/schedule/day?date={MONDAY}&doctorId={clinic_b.doctor_id}",
        headers=clinic_a.headers,
    )
    missing = await client.get(
        f"{API}/clinic/schedule/day?date={MONDAY}&doctorId={uuid.uuid4()}",
        headers=clinic_a.headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json() == missing.json()
    assert clinic_b.doctor_id not in resp.text


async def test_slots_with_doctor_id_from_other_clinic_denies(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Слоты врача ЧУЖОЙ клиники не отдаются — тот же 404, что и на несуществующего."""
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_b.doctor_id}",
        headers=clinic_a.headers,
    )
    missing = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={uuid.uuid4()}",
        headers=clinic_a.headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json() == missing.json()
    assert "slots" not in resp.json()


async def test_get_doctor_hours_of_other_clinic_returns_404(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """График врача ЧУЖОЙ клиники не читается."""
    resp = await client.get(
        f"{API}/clinic/schedule/doctors/{clinic_b.doctor_id}/hours", headers=clinic_a.headers
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "doctor_not_found"


async def test_put_doctor_hours_of_other_clinic_denies_and_does_not_change_it(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Правка графика ЧУЖОГО врача отклоняется и график остаётся прежним."""
    before = await client.get(
        f"{API}/clinic/schedule/doctors/{clinic_b.doctor_id}/hours", headers=clinic_b.headers
    )
    resp = await client.put(
        f"{API}/clinic/schedule/doctors/{clinic_b.doctor_id}/hours",
        headers=clinic_a.headers,
        json=doctor_hours_payload(starts="07:00:00", ends="08:00:00"),
    )
    assert resp.status_code in {403, 404}, resp.text

    after = await client.get(
        f"{API}/clinic/schedule/doctors/{clinic_b.doctor_id}/hours", headers=clinic_b.headers
    )
    assert after.json() == before.json()


async def test_booking_doctor_from_other_clinic_denied(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Запись к врачу ЧУЖОЙ клиники не создаётся."""
    resp = await _book(
        client, clinic_a, start=at(MONDAY, "10:00"), doctor=clinic_b.doctor_id, duration=30
    )
    assert resp.status_code in {403, 404, 422}, resp.text
    assert resp.status_code != 201


async def test_day_does_not_show_appointments_of_other_clinic(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """«Лента дня» клиники A не содержит ни приёмов, ни врачей клиники B."""
    booked = await _book(client, clinic_b, start=at(MONDAY, "10:00"), duration=30)
    assert booked.status_code == 201, booked.text

    resp = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["appointments"] == []
    ids = {d["membershipId"] for d in body["doctors"]}
    assert ids == {clinic_a.doctor_id, clinic_a.doctor2_id}


# ── 2. RBAC ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/clinic/schedule/day?date={MONDAY}"),
        ("GET", f"/clinic/schedule/slots?date={MONDAY}&doctorId={uuid.uuid4()}"),
        ("GET", "/clinic/schedule/working-hours"),
        ("PUT", "/clinic/schedule/working-hours"),
        ("GET", f"/clinic/schedule/doctors/{uuid.uuid4()}/hours"),
        ("PUT", f"/clinic/schedule/doctors/{uuid.uuid4()}/hours"),
    ],
)
async def test_schedule_endpoints_without_token_return_401(
    client: AsyncClient, method: str, path: str
) -> None:
    """Все ручки расписания закрыты авторизацией."""
    resp = await client.request(method, f"{API}{path}", json={"days": []})
    assert resp.status_code == 401, resp.text


async def test_worker_sees_own_schedule_but_not_others(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Роль «работник» (schedule.view_own): видит СВОЁ расписание, чужое — 403.

    Регрессия против прежней дыры: раньше здесь фиксировался 403 на day/slots/hours,
    и врач с ``appointments.create`` записывал вслепую — не мог посмотреть свободные
    слоты. Теперь own-контур открыт, но строго сведён к собственному членству, а
    чужие врачи по-прежнему недоступны.
    """
    own_id, phone = await _add_doctor(client, clinic_a.headers, "Работник")
    worker = await otp_login(client, phone)

    # /auth/me отдаёт собственный membershipId — фронт узнаёт свой id для doctorId.
    me = await client.get(f"{API}/auth/me", headers=worker)
    assert me.status_code == 200, me.text
    assert me.json()["activeClinic"]["membershipId"] == own_id

    # «Лента дня» сведена к своей колонке — чужих врачей в выдаче нет.
    day = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=worker)
    assert day.status_code == 200, day.text
    assert {d["membershipId"] for d in day.json()["doctors"]} == {own_id}

    # Явный запрос своей колонки — тоже 200; чужой doctorId в day — 403.
    own_day = await client.get(
        f"{API}/clinic/schedule/day?date={MONDAY}&doctorId={own_id}", headers=worker
    )
    assert own_day.status_code == 200, own_day.text
    other_day = await client.get(
        f"{API}/clinic/schedule/day?date={MONDAY}&doctorId={clinic_a.doctor_id}", headers=worker
    )
    assert other_day.status_code == 403, other_day.text
    assert other_day.json()["error"] == "not_own_schedule"
    assert clinic_a.doctor_id not in other_day.text

    # Свои слоты видны (главное — врач больше не записывает вслепую).
    own_slots = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={own_id}", headers=worker
    )
    assert own_slots.status_code == 200, own_slots.text
    assert "slots" in own_slots.json()

    # Чужие слоты — 403.
    other_slots = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_a.doctor_id}", headers=worker
    )
    assert other_slots.status_code == 403, other_slots.text
    assert other_slots.json()["error"] == "not_own_schedule"

    # Свой график читается; чужой — 403.
    own_hours = await client.get(
        f"{API}/clinic/schedule/doctors/{own_id}/hours", headers=worker
    )
    assert own_hours.status_code == 200, own_hours.text
    other_hours = await client.get(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/hours", headers=worker
    )
    assert other_hours.status_code == 403, other_hours.text
    assert other_hours.json()["error"] == "not_own_schedule"


async def test_worker_manages_own_schedule_not_others_or_clinic_settings(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Работник (schedule.manage_own) правит СВОЙ график, но не чужой и не часы клиники.

    manage_own покрывает только собственное членство; график других врачей остаётся
    за manage_all, а часы клиники — за settings.edit/settings.view, которых у роли нет.
    """
    own_id, phone = await _add_doctor(client, clinic_a.headers, "Работник 2")
    worker = await otp_login(client, phone)

    # Свой график — правится и сохраняется.
    own = await client.put(
        f"{API}/clinic/schedule/doctors/{own_id}/hours",
        headers=worker,
        json=doctor_hours_payload(starts="09:00:00", ends="12:00:00"),
    )
    assert own.status_code == 200, own.text
    assert own.json()["days"][0]["startsAt"] == "09:00:00"

    # Чужой график — 403 (manage_own не покрывает других врачей).
    other = await client.put(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/hours",
        headers=worker,
        json=doctor_hours_payload(starts="09:00:00", ends="12:00:00"),
    )
    assert other.status_code == 403, other.text
    assert other.json()["error"] == "not_own_schedule"

    # Часы клиники — settings.edit/settings.view, которых у работника нет.
    working = await client.put(
        f"{API}/clinic/schedule/working-hours", headers=worker, json=hours_payload()
    )
    assert working.status_code == 403, working.text
    assert (
        await client.get(f"{API}/clinic/schedule/working-hours", headers=worker)
    ).status_code == 403


# ── 3. Пересечения приёмов ──────────────────────────────────────────────────


async def test_overlapping_appointments_of_same_doctor_return_409(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Второй приём, налезающий на первый, — 409 slot_taken."""
    first = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45)
    assert first.status_code == 201, first.text

    second = await _book(client, clinic_a, start=at(MONDAY, "09:30"), duration=30)
    assert second.status_code == 409, second.text
    assert second.json()["error"] == "slot_taken"


async def test_back_to_back_appointments_are_allowed(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Приёмы встык (09:00–09:45 и 09:45–10:30) пересечением НЕ считаются."""
    first = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45)
    assert first.status_code == 201, first.text

    second = await _book(client, clinic_a, start=at(MONDAY, "09:45"), duration=45)
    assert second.status_code == 201, second.text


async def test_cancelled_appointment_frees_the_slot(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Отменённый приём слот не занимает — на его место можно записать снова."""
    first = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45)
    assert first.status_code == 201, first.text
    cancelled = await client.patch(
        f"{API}/clinic/appointments/{first.json()['id']}",
        headers=clinic_a.headers,
        json={"status": "cancelled", "cancelReason": "patient_request"},
    )
    assert cancelled.status_code == 200, cancelled.text

    again = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45)
    assert again.status_code == 201, again.text


async def test_appointments_without_doctor_do_not_conflict(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Приём без врача слот не занимает: два таких приёма в одно время — ок."""
    first = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45, doctor=None)
    second = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45, doctor=None)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text


async def test_same_interval_for_different_doctors_is_allowed(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Один и тот же интервал у РАЗНЫХ врачей — не конфликт."""
    first = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45)
    second = await _book(
        client, clinic_a, start=at(MONDAY, "09:00"), duration=45, doctor=clinic_a.doctor2_id
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text


async def test_moving_appointment_onto_busy_slot_returns_409(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Перенос приёма на занятое время — 409, а не молчаливое пересечение."""
    await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45)
    second = await _book(client, clinic_a, start=at(MONDAY, "11:00"), duration=45)
    assert second.status_code == 201, second.text

    moved = await client.patch(
        f"{API}/clinic/appointments/{second.json()['id']}",
        headers=clinic_a.headers,
        json={"scheduledAt": at(MONDAY, "09:15")},
    )
    assert moved.status_code == 409, moved.text


async def test_db_constraint_rejects_overlap_even_bypassing_application_check(
    _resources: Resources, client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """EXCLUDE-констрейнт живой: пересечение не пройдёт даже мимо прикладной проверки."""
    async with _resources.factory() as session:
        session.add(
            Appointment(
                clinic_id=uuid.UUID(clinic_a.clinic_id),
                patient_id=uuid.UUID(clinic_a.patient_id),
                doctor_membership_id=uuid.UUID(clinic_a.doctor_id),
                scheduled_at=datetime.fromisoformat(at(MONDAY, "09:00")),
                duration_min=45,
                status=AppointmentStatus.SCHEDULED,
            )
        )
        await session.commit()

    async with _resources.factory() as session:
        session.add(
            Appointment(
                clinic_id=uuid.UUID(clinic_a.clinic_id),
                patient_id=uuid.UUID(clinic_a.patient_id),
                doctor_membership_id=uuid.UUID(clinic_a.doctor_id),
                scheduled_at=datetime.fromisoformat(at(MONDAY, "09:30")),
                duration_min=45,
                status=AppointmentStatus.SCHEDULED,
            )
        )
        with pytest.raises(IntegrityError, match="ex_appointment_doctor_overlap"):
            await session.commit()


async def test_booking_guard_turns_constraint_violation_into_conflict_and_frees_session(
    _resources: Resources, clinic_a: ClinicCtx
) -> None:
    """booking_guard ловит ExclusionViolationError из flush() и откатывает сессию.

    Гонку из теста выше прикладная проверка часто выигрывает сама, поэтому по коду
    ответа не понять, сработал ли гвард. Здесь констрейнт нарушается заведомо —
    видно и превращение в 409, и откат: без rollback следующая операция на этом
    соединении падала бы DBAPIError («транзакция в failed-состоянии»).
    """
    async with _resources.factory() as session:
        session.add(
            Appointment(
                clinic_id=uuid.UUID(clinic_a.clinic_id),
                patient_id=uuid.UUID(clinic_a.patient_id),
                doctor_membership_id=uuid.UUID(clinic_a.doctor_id),
                scheduled_at=datetime.fromisoformat(at(MONDAY, "09:00")),
                duration_min=45,
                status=AppointmentStatus.SCHEDULED,
            )
        )
        await session.commit()

    async with _resources.factory() as session:
        with pytest.raises(ConflictError) as raised:
            async with booking_guard(session):
                session.add(
                    Appointment(
                        clinic_id=uuid.UUID(clinic_a.clinic_id),
                        patient_id=uuid.UUID(clinic_a.patient_id),
                        doctor_membership_id=uuid.UUID(clinic_a.doctor_id),
                        scheduled_at=datetime.fromisoformat(at(MONDAY, "09:30")),
                        duration_min=45,
                        status=AppointmentStatus.SCHEDULED,
                    )
                )
                await session.commit()
        assert raised.value.code == "slot_taken"
        assert raised.value.status_code == 409

        # Сессия откачена и пригодна к работе, а конфликтующий приём не сохранился.
        rows = (
            await session.execute(
                select(Appointment.id).where(Appointment.clinic_id == uuid.UUID(clinic_a.clinic_id))
            )
        ).all()
        assert len(rows) == 1


async def test_concurrent_booking_of_one_slot_returns_409_not_500(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Гонка за один слот: ровно одна запись создаётся, остальные — 409, и никаких 500."""
    # Прогрев пула соединений: на «холодном» батче прикладная проверка обычно
    # успевает отработать и до констрейнта дело не доходит.
    await asyncio.gather(
        *[_book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30) for _ in range(8)],
        return_exceptions=True,
    )
    responses = await asyncio.gather(
        *[_book(client, clinic_a, start=at(MONDAY, "10:00"), duration=30) for _ in range(8)],
        return_exceptions=True,
    )
    crashed = [r for r in responses if isinstance(r, BaseException)]
    assert not crashed, f"ожидался 409, а не необработанное исключение: {crashed!r}"
    statuses = sorted(r.status_code for r in responses if not isinstance(r, BaseException))
    assert statuses == [201] + [409] * 7


async def test_uncancelling_appointment_onto_taken_slot_returns_409(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Возврат отменённого приёма на уже занятый слот — штатный 409, а не 500.

    Снятие отмены — это откат: он разрешён только для приёма ТЕКУЩЕГО дня и
    только с комментарием, поэтому приём заводится на сегодня (часы работы
    открываем на все семь дней, чтобы тест не зависел от дня недели).
    """
    await client.put(
        f"{API}/clinic/schedule/working-hours",
        headers=clinic_a.headers,
        json=hours_payload(working=tuple(range(7))),
    )
    slot = at(today(), "09:00")
    first = await _book(client, clinic_a, start=slot, duration=45)
    assert first.status_code == 201, first.text
    cancelled = await client.patch(
        f"{API}/clinic/appointments/{first.json()['id']}",
        headers=clinic_a.headers,
        json={"status": "cancelled", "cancelReason": "patient_request"},
    )
    assert cancelled.status_code == 200, cancelled.text

    # Слот освободился и занят другим приёмом.
    second = await _book(client, clinic_a, start=slot, duration=45)
    assert second.status_code == 201, second.text

    # Теперь снять отмену нельзя — слот занят.
    back = await client.patch(
        f"{API}/clinic/appointments/{first.json()['id']}",
        headers=clinic_a.headers,
        json={"status": "scheduled", "rollbackComment": "Пациент вернулся"},
    )
    assert back.status_code == 409, back.text
    assert back.json()["error"] == "slot_taken"


# ── 4. Таймзона и границы дня ───────────────────────────────────────────────


async def test_late_night_appointment_stays_in_its_own_clinic_day(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Приём в 23:30 по таймзоне клиники виден в СВОЁМ дне, а не в соседнем."""
    await client.put(
        f"{API}/clinic/schedule/working-hours",
        headers=clinic_a.headers,
        json=hours_payload(
            opens="00:00:00",
            closes="23:59:59",
            break_start=None,
            break_end=None,
            working=(0, 1, 2, 3, 4, 5, 6),
        ),
    )
    booked = await _book(client, clinic_a, start=at(MONDAY, "23:30"), duration=29)
    assert booked.status_code == 201, booked.text

    same_day = await client.get(
        f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers
    )
    assert [a["id"] for a in same_day.json()["appointments"]] == [booked.json()["id"]]
    assert same_day.json()["appointments"][0]["startAt"] == at(MONDAY, "23:30")

    next_day = await client.get(
        f"{API}/clinic/schedule/day?date={TUESDAY}", headers=clinic_a.headers
    )
    assert next_day.json()["appointments"] == []


async def test_early_morning_appointment_belongs_to_local_day_not_utc_day(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """00:30 по Бишкеку = 18:30 UTC предыдущих суток — приём обязан остаться в своём дне."""
    await client.put(
        f"{API}/clinic/schedule/working-hours",
        headers=clinic_a.headers,
        json=hours_payload(
            opens="00:00:00",
            closes="23:59:59",
            break_start=None,
            break_end=None,
            working=(0, 1, 2, 3, 4, 5, 6),
        ),
    )
    booked = await _book(client, clinic_a, start=at(MONDAY, "00:30"), duration=30)
    assert booked.status_code == 201, booked.text

    same_day = await client.get(
        f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers
    )
    assert [a["id"] for a in same_day.json()["appointments"]] == [booked.json()["id"]]

    previous_day = await client.get(
        f"{API}/clinic/schedule/day?date={SUNDAY}", headers=clinic_a.headers
    )
    assert previous_day.json()["appointments"] == []


async def test_day_grid_and_cards_share_the_clinic_timezone_offset(
    _resources: Resources, client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Сетка и карточки отдаются в одном представлении — в таймзоне клиники, а не в UTC."""
    await _set_timezone(_resources, clinic_a.clinic_id, "Europe/Moscow")
    moscow = ZoneInfo("Europe/Moscow")

    booked = await _book(client, clinic_a, start=at(MONDAY, "10:00", moscow), duration=30)
    assert booked.status_code == 201, booked.text

    resp = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["timezone"] == "Europe/Moscow"
    assert body["opensAt"] == at(MONDAY, "09:00", moscow)
    assert body["closesAt"] == at(MONDAY, "18:00", moscow)
    assert body["break"]["startAt"] == at(MONDAY, "12:00", moscow)

    card = body["appointments"][0]
    assert card["startAt"] == at(MONDAY, "10:00", moscow)
    assert card["endAt"] == at(MONDAY, "10:30", moscow)
    for value in (body["opensAt"], card["startAt"], card["endAt"]):
        assert value.endswith("+03:00"), value


# ── 5. Нерабочее время → 422 ────────────────────────────────────────────────


async def test_booking_during_break_is_rejected(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """Обед 12:00–13:00 записи не принимает."""
    resp = await _book(client, clinic_a, start=at(MONDAY, "12:15"), duration=30)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "break_time"


async def test_booking_before_opening_is_rejected(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """До открытия (08:00 при часах 09:00–18:00) записать нельзя."""
    resp = await _book(client, clinic_a, start=at(MONDAY, "08:00"), duration=30)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "outside_working_hours"


async def test_booking_running_past_closing_is_rejected(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Приём, вылезающий за закрытие (17:45 + 30 мин), не принимается целиком."""
    resp = await _book(client, clinic_a, start=at(MONDAY, "17:45"), duration=30)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "outside_working_hours"


async def test_booking_on_clinic_day_off_is_rejected(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Воскресенье по умолчанию выходной — записи нет."""
    resp = await _book(client, clinic_a, start=at(SUNDAY, "10:00"), duration=30)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "outside_working_hours"


async def test_booking_during_doctor_time_off_is_rejected(
    _resources: Resources, client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Отсутствие врача (doctor_time_off) перекрывает рабочее окно."""
    await _add_time_off(
        _resources, clinic_a.clinic_id, clinic_a.doctor_id, at(MONDAY, "09:00"), at(MONDAY, "11:00")
    )
    resp = await _book(client, clinic_a, start=at(MONDAY, "10:00"), duration=30)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "doctor_time_off"

    # Вне отсутствия тот же день записывается штатно.
    ok = await _book(client, clinic_a, start=at(MONDAY, "11:00"), duration=30)
    assert ok.status_code == 201, ok.text


async def test_booking_outside_doctor_schedule_is_rejected(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """График врача сужает часы клиники: 14:00 вне смены 09:00–12:00 → отказ."""
    saved = await client.put(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/hours",
        headers=clinic_a.headers,
        json=doctor_hours_payload(starts="09:00:00", ends="12:00:00"),
    )
    assert saved.status_code == 200, saved.text

    late = await _book(client, clinic_a, start=at(MONDAY, "14:00"), duration=30)
    assert late.status_code == 422, late.text
    assert late.json()["error"] == "outside_working_hours"

    # Другой врач без личного графика работает по часам клиники — 14:00 ему доступно.
    other = await _book(
        client, clinic_a, start=at(MONDAY, "14:00"), duration=30, doctor=clinic_a.doctor2_id
    )
    assert other.status_code == 201, other.text


# ── 6. Слоты ────────────────────────────────────────────────────────────────


async def test_slots_use_default_grid_and_cut_out_the_break(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """По умолчанию: слоты 30 мин с шагом 15, обед 12:00–13:00 вырезан."""
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_a.doctor_id}",
        headers=clinic_a.headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["durationMin"] == 30
    assert body["timezone"] == "Asia/Bishkek"

    starts = [s["startAt"] for s in body["slots"]]
    assert starts[0] == at(MONDAY, "09:00")
    assert at(MONDAY, "11:30") in starts  # последний до обеда
    assert at(MONDAY, "13:00") in starts  # первый после обеда
    assert starts[-1] == at(MONDAY, "17:30")
    # Ни один слот не залезает на обед.
    for slot in body["slots"]:
        assert not (slot["startAt"] < at(MONDAY, "13:00") and slot["endAt"] > at(MONDAY, "12:00"))


async def test_slots_exclude_busy_intervals(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """Занятый приёмом интервал исчезает из слотов, соседний встык — остаётся."""
    before = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_a.doctor_id}",
        headers=clinic_a.headers,
    )
    assert at(MONDAY, "09:00") in [s["startAt"] for s in before.json()["slots"]]

    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=60)
    assert booked.status_code == 201, booked.text

    after = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_a.doctor_id}",
        headers=clinic_a.headers,
    )
    starts = [s["startAt"] for s in after.json()["slots"]]
    for busy in ("09:00", "09:15", "09:30", "09:45"):
        assert at(MONDAY, busy) not in starts
    assert starts[0] == at(MONDAY, "10:00")


async def test_slots_are_narrowed_by_doctor_schedule(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """График врача уже часов клиники — окно сужается, за его край слотов нет."""
    await client.put(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/hours",
        headers=clinic_a.headers,
        json=doctor_hours_payload(starts="10:00:00", ends="12:00:00"),
    )
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_a.doctor_id}",
        headers=clinic_a.headers,
    )
    starts = [s["startAt"] for s in resp.json()["slots"]]
    assert starts[0] == at(MONDAY, "10:00")
    assert starts[-1] == at(MONDAY, "11:30")


async def test_slots_duration_comes_from_service(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """serviceId задаёт длину слота из Service.duration."""
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}"
        f"&doctorId={clinic_a.doctor_id}&serviceId={clinic_a.service45_id}",
        headers=clinic_a.headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["durationMin"] == 45
    first = body["slots"][0]
    assert first["startAt"] == at(MONDAY, "09:00")
    assert first["endAt"] == at(MONDAY, "09:45")
    # Последний слот до обеда должен заканчиваться ровно в 12:00.
    before_break = [s for s in body["slots"] if s["endAt"] <= at(MONDAY, "12:00")]
    assert before_break[-1]["endAt"] == at(MONDAY, "12:00")


async def test_slots_with_service_from_other_clinic_denied(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Услуга ЧУЖОЙ клиники в slots — отказ (IDOR по услуге)."""
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}"
        f"&doctorId={clinic_a.doctor_id}&serviceId={clinic_b.service45_id}",
        headers=clinic_a.headers,
    )
    assert resp.status_code in {403, 404, 422}, resp.text
    assert resp.json()["error"] == "invalid_service"


# ── 7. Часы работы: дефолт, сохранение, валидация ───────────────────────────


async def test_working_hours_defaults_when_clinic_not_configured(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Ненастроенная клиника: пн–сб 09:00–18:00, обед 12:00–13:00, вс — выходной."""
    resp = await client.get(f"{API}/clinic/schedule/working-hours", headers=clinic_a.headers)
    assert resp.status_code == 200, resp.text
    days = resp.json()["days"]
    assert [d["weekday"] for d in days] == list(range(7))
    for day in days[:6]:
        assert day["isWorking"] is True
        assert (day["opensAt"], day["closesAt"]) == ("09:00:00", "18:00:00")
        assert (day["breakStart"], day["breakEnd"]) == ("12:00:00", "13:00:00")
    assert days[6]["isWorking"] is False


async def test_working_hours_round_trip(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """Сохранённые часы возвращаются GET-ом и применяются к «ленте дня»."""
    saved = await client.put(
        f"{API}/clinic/schedule/working-hours",
        headers=clinic_a.headers,
        json=hours_payload(opens="08:00:00", closes="16:00:00", break_start=None, break_end=None),
    )
    assert saved.status_code == 200, saved.text

    read = await client.get(f"{API}/clinic/schedule/working-hours", headers=clinic_a.headers)
    assert read.json()["days"][0]["opensAt"] == "08:00:00"
    assert read.json()["days"][0]["breakStart"] is None

    day = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    assert day.json()["opensAt"] == at(MONDAY, "08:00")
    assert day.json()["closesAt"] == at(MONDAY, "16:00")
    assert day.json()["break"] is None


async def test_day_off_in_clinic_hours_makes_day_non_working(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Понедельник помечен нерабочим — сетка пустая, врачи не работают."""
    await client.put(
        f"{API}/clinic/schedule/working-hours",
        headers=clinic_a.headers,
        json=hours_payload(working=(1, 2, 3, 4, 5)),
    )
    resp = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    body = resp.json()
    assert body["isWorking"] is False
    assert body["opensAt"] is None and body["closesAt"] is None
    assert body["break"] is None
    assert all(d["isWorking"] is False for d in body["doctors"])


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (hours_payload(opens="18:00:00", closes="09:00:00"), "закрытие раньше открытия"),
        (hours_payload(opens="09:00:00", closes="09:00:00"), "нулевое окно"),
        (
            hours_payload(break_start="08:00:00", break_end="08:30:00"),
            "обед до открытия",
        ),
        (
            hours_payload(break_start="17:00:00", break_end="19:00:00"),
            "обед вылезает за закрытие",
        ),
        (hours_payload(break_start="13:00:00", break_end="12:00:00"), "обед задом наперёд"),
        (hours_payload(break_start="12:00:00", break_end=None), "одна граница обеда"),
    ],
)
async def test_working_hours_invalid_payload_returns_422(
    client: AsyncClient, clinic_a: ClinicCtx, payload: dict[str, Any], reason: str
) -> None:
    """Некорректная сетка часов работы отклоняется с 422."""
    resp = await client.put(
        f"{API}/clinic/schedule/working-hours", headers=clinic_a.headers, json=payload
    )
    assert resp.status_code == 422, f"{reason}: {resp.text}"


async def test_working_hours_wrong_weekday_or_day_count_returns_422(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """weekday вне 0..6 и неполная неделя — 422."""
    bad_weekday = hours_payload()
    bad_weekday["days"][6]["weekday"] = 7
    resp = await client.put(
        f"{API}/clinic/schedule/working-hours", headers=clinic_a.headers, json=bad_weekday
    )
    assert resp.status_code == 422, resp.text

    short = hours_payload()
    short["days"] = short["days"][:6]
    resp = await client.put(
        f"{API}/clinic/schedule/working-hours", headers=clinic_a.headers, json=short
    )
    assert resp.status_code == 422, resp.text

    duplicated = hours_payload()
    duplicated["days"][6]["weekday"] = 0
    resp = await client.put(
        f"{API}/clinic/schedule/working-hours", headers=clinic_a.headers, json=duplicated
    )
    assert resp.status_code == 422, resp.text


async def test_doctor_hours_invalid_shift_returns_422(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Конец смены не позже начала — 422."""
    resp = await client.put(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/hours",
        headers=clinic_a.headers,
        json=doctor_hours_payload(starts="15:00:00", ends="09:00:00"),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_doctor_hours"


async def test_day_rejects_malformed_date(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """Некорректная дата — 422, а не 500."""
    resp = await client.get(f"{API}/clinic/schedule/day?date=2026-13-45", headers=clinic_a.headers)
    assert resp.status_code == 422, resp.text


async def test_naive_scheduled_at_is_rejected_not_500(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """scheduledAt без таймзоны — понятная ошибка валидации, а не падение сервера."""
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=clinic_a.headers,
        json={
            "patientId": clinic_a.patient_id,
            "doctorMembershipId": clinic_a.doctor_id,
            "scheduledAt": "2026-07-20T09:00:00",
            "durationMin": 30,
        },
    )
    assert resp.status_code == 422, resp.text


async def test_naive_scheduled_at_rejected_even_without_doctor(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Naive-время отвергается и БЕЗ врача — раньше этот путь молча сохранял приём.

    Проверка рабочего окна выполняется только при назначенном враче, поэтому naive
    ``scheduledAt`` без врача доезжал до БД и трактовался по таймзоне хоста: на машине
    в Asia/Bishkek приём случайно ложился верно, на CI в UTC — уезжал на 6 часов.
    Отсечка на границе API делает ассерт переносимым: 422 не зависит от таймзоны хоста.
    """
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=clinic_a.headers,
        json={
            "patientId": clinic_a.patient_id,
            "scheduledAt": "2026-07-20T09:00:00",
            "durationMin": 30,
        },
    )
    assert resp.status_code == 422, resp.text

    # И приём действительно не создан — «тихой» записи не осталось.
    day = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    assert day.json()["appointments"] == []


async def test_naive_scheduled_at_rejected_on_update(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Перенос приёма на naive-время тоже отвергается на границе API."""
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30)
    assert booked.status_code == 201, booked.text

    resp = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=clinic_a.headers,
        json={"scheduledAt": "2026-07-20T14:00:00"},
    )
    assert resp.status_code == 422, resp.text


# ── 8. Длительность приёма ──────────────────────────────────────────────────


async def test_duration_defaults_to_service_duration(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """durationMin не передан, но есть услуга → длительность из Service.duration."""
    booked = await _book(
        client, clinic_a, start=at(MONDAY, "09:00"), service_id=clinic_a.service45_id
    )
    assert booked.status_code == 201, booked.text

    detail = await client.get(
        f"{API}/clinic/appointments/{booked.json()['id']}", headers=clinic_a.headers
    )
    assert detail.json()["durationMin"] == 45

    day = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    card = day.json()["appointments"][0]
    assert card["endAt"] == at(MONDAY, "09:45")
    assert card["serviceName"] == "Лечение 45"


async def test_duration_defaults_to_30_without_service(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Ни услуги, ни durationMin → 30 минут."""
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"))
    assert booked.status_code == 201, booked.text
    detail = await client.get(
        f"{API}/clinic/appointments/{booked.json()['id']}", headers=clinic_a.headers
    )
    assert detail.json()["durationMin"] == 30


async def test_explicit_duration_wins_over_service(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Явный durationMin важнее длительности услуги."""
    booked = await _book(
        client,
        clinic_a,
        start=at(MONDAY, "09:00"),
        duration=15,
        service_id=clinic_a.service45_id,
    )
    assert booked.status_code == 201, booked.text
    detail = await client.get(
        f"{API}/clinic/appointments/{booked.json()['id']}", headers=clinic_a.headers
    )
    assert detail.json()["durationMin"] == 15


async def test_duration_from_service_without_duration_falls_back_to_30(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """У услуги не задана длительность → дефолт 30, а не падение."""
    service = await _add_service(client, clinic_a.headers, "Осмотр без длительности", None)
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), service_id=service)
    assert booked.status_code == 201, booked.text
    detail = await client.get(
        f"{API}/clinic/appointments/{booked.json()['id']}", headers=clinic_a.headers
    )
    assert detail.json()["durationMin"] == 30


# ── 9. Календарь дня: счётчик и содержимое карточек ─────────────────────────


async def test_day_counts_appointments_per_doctor_for_that_day_only(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """appointmentsCount — приёмы ЭТОГО врача за ЭТОТ день (соседний день не считается)."""
    for start in ("09:00", "10:00", "11:00"):
        resp = await _book(client, clinic_a, start=at(MONDAY, start), duration=30)
        assert resp.status_code == 201, resp.text
    other = await _book(
        client, clinic_a, start=at(MONDAY, "09:00"), duration=30, doctor=clinic_a.doctor2_id
    )
    assert other.status_code == 201, other.text
    # Приём того же врача в другой день в счётчик понедельника попасть не должен.
    tuesday = await _book(client, clinic_a, start=at(TUESDAY, "09:00"), duration=30)
    assert tuesday.status_code == 201, tuesday.text

    resp = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    counts = {d["membershipId"]: d["appointmentsCount"] for d in resp.json()["doctors"]}
    assert counts[clinic_a.doctor_id] == 3
    assert counts[clinic_a.doctor2_id] == 1


async def test_day_card_carries_patient_and_working_window(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Карточка несёт имя пациента и интервал; колонка врача — рабочее окно."""
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45)
    assert booked.status_code == 201, booked.text

    resp = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    body = resp.json()
    assert body["date"] == MONDAY
    assert body["isWorking"] is True

    card = body["appointments"][0]
    assert card["patientId"] == clinic_a.patient_id
    assert card["patientName"] == "Тестовый Пациент Клиника A"
    assert card["startAt"] == at(MONDAY, "09:00")
    assert card["endAt"] == at(MONDAY, "09:45")
    assert card["status"] == "scheduled"

    column = next(d for d in body["doctors"] if d["membershipId"] == clinic_a.doctor_id)
    assert column["isWorking"] is True
    assert column["worksFrom"] == at(MONDAY, "09:00")
    assert column["worksTo"] == at(MONDAY, "18:00")


async def test_day_doctor_window_reflects_full_day_time_off(
    _resources: Resources, client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Отсутствие, накрывающее весь день, делает колонку врача нерабочей."""
    await _add_time_off(
        _resources,
        clinic_a.clinic_id,
        clinic_a.doctor_id,
        at(MONDAY, "00:00"),
        at(TUESDAY, "00:00"),
    )
    resp = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    columns = {d["membershipId"]: d for d in resp.json()["doctors"]}
    assert columns[clinic_a.doctor_id]["isWorking"] is False
    assert columns[clinic_a.doctor_id]["worksFrom"] is None
    assert columns[clinic_a.doctor2_id]["isWorking"] is True


async def test_day_filtered_by_own_doctor_returns_only_his_column(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Фильтр по своему врачу оставляет одну колонку и только его приёмы."""
    mine = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30)
    other = await _book(
        client, clinic_a, start=at(MONDAY, "10:00"), duration=30, doctor=clinic_a.doctor2_id
    )
    assert mine.status_code == 201 and other.status_code == 201

    resp = await client.get(
        f"{API}/clinic/schedule/day?date={MONDAY}&doctorId={clinic_a.doctor_id}",
        headers=clinic_a.headers,
    )
    body = resp.json()
    assert [d["membershipId"] for d in body["doctors"]] == [clinic_a.doctor_id]
    assert [a["id"] for a in body["appointments"]] == [mine.json()["id"]]


async def test_doctor_hours_default_to_clinic_hours(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Без личного графика врач работает по часам клиники (все 7 дней в ответе)."""
    await client.put(
        f"{API}/clinic/schedule/working-hours",
        headers=clinic_a.headers,
        json=hours_payload(opens="08:00:00", closes="16:00:00"),
    )
    resp = await client.get(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/hours", headers=clinic_a.headers
    )
    assert resp.status_code == 200, resp.text
    days = resp.json()["days"]
    assert [d["weekday"] for d in days] == list(range(7))
    assert (days[0]["startsAt"], days[0]["endsAt"]) == ("08:00:00", "16:00:00")
    assert days[6]["isWorking"] is False


# ── 10. Регресс-проверки правок после ревью ─────────────────────────────────


async def test_completing_appointment_outside_current_hours_is_not_blocked(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Смена статуса на completed НЕ гоняет проверку рабочего окна.

    Мотив предиката _affects_booking(): приём заведён по старым часам клиники, потом
    часы сузили. Завершать такой приём нужно уметь — иначе ложный 422 на историческом
    приёме, который уже состоялся.
    """
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30)
    assert booked.status_code == 201, booked.text

    # Часы сузились: 09:00 теперь вне рабочего окна.
    narrowed = await client.put(
        f"{API}/clinic/schedule/working-hours",
        headers=clinic_a.headers,
        json=hours_payload(opens="14:00:00", closes="18:00:00", break_start=None, break_end=None),
    )
    assert narrowed.status_code == 200, narrowed.text
    # Новую запись на 09:00 клиника уже не принимает…
    rejected = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30)
    assert rejected.status_code == 422, rejected.text

    # …но старый приём завершается штатно (позиций нет — закрываем как бесплатный).
    completed = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=clinic_a.headers,
        json={
            "status": "completed",
            "noCharge": True,
            "noChargeComment": "Осмотр без назначений",
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "completed"


async def test_uncancelling_appointment_into_free_slot_succeeds(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Снятие отмены на свободный слот проходит — проверка не стала слишком строгой.

    Это откат, поэтому нужен приём текущего дня и комментарий (право на откат у
    владельца есть). Часы работы открываем на все семь дней, чтобы тест не
    зависел от дня недели.
    """
    await client.put(
        f"{API}/clinic/schedule/working-hours",
        headers=clinic_a.headers,
        json=hours_payload(working=tuple(range(7))),
    )
    booked = await _book(client, clinic_a, start=at(today(), "10:00"), duration=30)
    assert booked.status_code == 201, booked.text
    await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=clinic_a.headers,
        json={"status": "cancelled", "cancelReason": "patient_request"},
    )
    back = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=clinic_a.headers,
        json={"status": "scheduled", "rollbackComment": "Пациент вернулся"},
    )
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "scheduled"


async def test_slots_stay_on_the_quarter_hour_grid_after_odd_appointment(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Приём 09:00–09:50 не сдвигает сетку: следующий слот 10:00, а не 09:50."""
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=50)
    assert booked.status_code == 201, booked.text

    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_a.doctor_id}",
        headers=clinic_a.headers,
    )
    starts = [s["startAt"] for s in resp.json()["slots"]]
    assert starts[:3] == [at(MONDAY, "10:00"), at(MONDAY, "10:15"), at(MONDAY, "10:30")]
    # Ни один слот не начинается вне сетки 15 минут.
    for value in starts:
        assert int(value[14:16]) % 15 == 0, value


async def test_slots_grid_starts_at_window_opening(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Окно 09:00–10:00 даёт ровно 09:00, 09:15, 09:30 — выравнивание не съедает первый слот."""
    await client.put(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/hours",
        headers=clinic_a.headers,
        json=doctor_hours_payload(starts="09:00:00", ends="10:00:00"),
    )
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_a.doctor_id}",
        headers=clinic_a.headers,
    )
    starts = [s["startAt"] for s in resp.json()["slots"]]
    assert starts == [at(MONDAY, "09:00"), at(MONDAY, "09:15"), at(MONDAY, "09:30")]


async def test_slots_grid_realigns_window_opening_off_the_step(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Смена 09:05–10:00: первый слот подтягивается к сетке (09:15), 09:05 не предлагается."""
    await client.put(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/hours",
        headers=clinic_a.headers,
        json=doctor_hours_payload(starts="09:05:00", ends="10:00:00"),
    )
    resp = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_a.doctor_id}",
        headers=clinic_a.headers,
    )
    # 09:15–09:45 и 09:30–10:00 помещаются в окно; 09:05 — вне сетки шага.
    starts = [s["startAt"] for s in resp.json()["slots"]]
    assert starts == [at(MONDAY, "09:15"), at(MONDAY, "09:30")]


async def test_booking_to_deactivated_doctor_is_rejected(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Уволенный (деактивированный) врач записи не принимает — как и колонки в day не имеет."""
    ok = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30)
    assert ok.status_code == 201, ok.text

    removed = await client.delete(
        f"{API}/clinic/staff/{clinic_a.doctor_id}", headers=clinic_a.headers
    )
    assert removed.status_code == 200, removed.text

    resp = await _book(client, clinic_a, start=at(MONDAY, "11:00"), duration=30)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_doctor"

    # Колонки календаря и проверка записи опираются на одно определение врача.
    day = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    assert [d["membershipId"] for d in day.json()["doctors"]] == [clinic_a.doctor2_id]


async def test_booking_to_owner_without_specialization_is_rejected(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Владелец без специализации — не практикующий врач: записи не принимает."""
    staff = await client.get(f"{API}/clinic/staff", headers=clinic_a.headers)
    owner = next(m for m in staff.json() if m["isOwner"])
    assert owner["specializationId"] is None

    resp = await _book(
        client, clinic_a, start=at(MONDAY, "09:00"), duration=30, doctor=owner["membershipId"]
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_doctor"


async def test_reassigning_appointment_to_deactivated_doctor_is_rejected(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """PATCH врача тоже ходит через единое определение врача."""
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30)
    assert booked.status_code == 201, booked.text
    await client.delete(f"{API}/clinic/staff/{clinic_a.doctor2_id}", headers=clinic_a.headers)

    resp = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=clinic_a.headers,
        json={"doctorMembershipId": clinic_a.doctor2_id},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_doctor"


async def test_uncancelling_straight_to_completed_is_forbidden(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Снять отмену можно только в ``scheduled`` — прыжок сразу в ``completed`` закрыт.

    Раньше такой переход разрешался и ловился лишь проверкой занятости слота.
    Теперь он запрещён самой воронкой: иначе отменённый приём восстанавливался бы
    сразу как финансовый факт (завершён = деньги в кассе), минуя рабочие статусы.
    """
    first = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45)
    assert first.status_code == 201, first.text
    await client.patch(
        f"{API}/clinic/appointments/{first.json()['id']}",
        headers=clinic_a.headers,
        json={"status": "cancelled", "cancelReason": "patient_request"},
    )
    second = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45)
    assert second.status_code == 201, second.text

    back = await client.patch(
        f"{API}/clinic/appointments/{first.json()['id']}",
        headers=clinic_a.headers,
        json={"status": "completed", "rollbackComment": "Всё-таки пришёл"},
    )
    assert back.status_code == 409, back.text
    assert back.json()["error"] == "invalid_status_transition"


# ── 11. NULL в NOT NULL-колонки: порча данных через PATCH ───────────────────
#
# Класс бага: поле схемы типизировано как `X | None` (чтобы «не передавать» его
# было можно), но колонка в БД NOT NULL. Явный `null` в теле проезжал валидацию,
# доезжал до setattr и коммитился — 500 клиент получал уже ПОСЛЕ порчи строки.
# Поэтому здесь везде проверяется не только код ответа, но и целостность данных.


@pytest.mark.parametrize(
    "field",
    ["scheduledAt", "status", "appointmentType", "durationMin"],
)
async def test_patch_null_into_not_null_field_is_rejected_and_row_intact(
    client: AsyncClient, clinic_a: ClinicCtx, field: str
) -> None:
    """null в NOT NULL-поле → 422, и строка в БД не изменилась."""
    booked = await _book(
        client, clinic_a, start=at(MONDAY, "09:00"), duration=45, service_id=clinic_a.service45_id
    )
    assert booked.status_code == 201, booked.text
    appointment_id = booked.json()["id"]
    before = (
        await client.get(f"{API}/clinic/appointments/{appointment_id}", headers=clinic_a.headers)
    ).json()

    resp = await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=clinic_a.headers,
        json={field: None},
    )
    assert resp.status_code == 422, f"{field}: {resp.text}"

    after = await client.get(
        f"{API}/clinic/appointments/{appointment_id}", headers=clinic_a.headers
    )
    assert after.status_code == 200, after.text
    assert after.json() == before, f"{field}: строка изменилась после отклонённого запроса"


async def test_patch_null_survives_worker_role(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """Дыра была доступна любому с appointments.edit — в т.ч. роли «работник»."""
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30)
    assert booked.status_code == 201, booked.text

    _, phone = await _add_doctor(client, clinic_a.headers, "Работник-вандал")
    worker = await otp_login(client, phone)

    resp = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=worker,
        json={"scheduledAt": None},
    )
    assert resp.status_code == 422, resp.text


async def test_clinic_survives_rejected_null_patch(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """После отклонённого null всё живо: списки, карточка, слоты, новая запись, лента дня.

    Раньше закоммиченный scheduled_at=NULL ломал клинику навсегда: выборки падали
    на NULL, а сам приём исчезал из «ленты дня» (предикат scheduled_at >= … молча
    отбрасывает NULL).
    """
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=45)
    assert booked.status_code == 201, booked.text
    appointment_id = booked.json()["id"]

    rejected = await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=clinic_a.headers,
        json={"scheduledAt": None},
    )
    assert rejected.status_code == 422, rejected.text

    listing = await client.get(f"{API}/clinic/appointments", headers=clinic_a.headers)
    assert listing.status_code == 200, listing.text
    assert appointment_id in [a["id"] for a in listing.json()["list"]]

    detail = await client.get(
        f"{API}/clinic/appointments/{appointment_id}", headers=clinic_a.headers
    )
    assert detail.status_code == 200, detail.text
    # Модуль приёмов отдаёт момент в UTC, расписание — в таймзоне клиники:
    # сравниваем сам момент, а не его строковое представление.
    assert datetime.fromisoformat(detail.json()["scheduledAt"]) == datetime.fromisoformat(
        at(MONDAY, "09:00")
    )

    slots = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_a.doctor_id}",
        headers=clinic_a.headers,
    )
    assert slots.status_code == 200, slots.text
    assert slots.json()["slots"], "слоты не должны схлопнуться в пустой список"

    again = await _book(client, clinic_a, start=at(MONDAY, "14:00"), duration=30)
    assert again.status_code == 201, again.text

    # Приём на месте в «ленте дня» — раньше он отсюда бесследно исчезал.
    day = await client.get(f"{API}/clinic/schedule/day?date={MONDAY}", headers=clinic_a.headers)
    assert day.status_code == 200, day.text
    assert appointment_id in [a["id"] for a in day.json()["appointments"]]


@pytest.mark.parametrize("field", ["patientId", "appointmentType", "items"])
async def test_post_null_into_required_field_is_rejected(
    client: AsyncClient, clinic_a: ClinicCtx, field: str
) -> None:
    """Тот же класс на создании: null в обязательное поле → 422, а не 500."""
    body: dict[str, Any] = {
        "patientId": clinic_a.patient_id,
        "scheduledAt": at(MONDAY, "09:00"),
        "durationMin": 30,
        field: None,
    }
    resp = await client.post(f"{API}/clinic/appointments", headers=clinic_a.headers, json=body)
    assert resp.status_code == 422, f"{field}: {resp.text}"


async def test_nullable_fields_still_accept_null(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """Запрет null не задел NULLABLE-колонки: врача, комментарий и услугу снять можно."""
    booked = await _book(
        client, clinic_a, start=at(MONDAY, "09:00"), duration=30, service_id=clinic_a.service45_id
    )
    assert booked.status_code == 201, booked.text

    resp = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=clinic_a.headers,
        json={"doctorMembershipId": None, "comment": None, "serviceId": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["serviceId"] is None
    assert resp.json()["doctorName"] is None


# ── 12. Границы длительности и снятие услуги ────────────────────────────────


async def test_absurd_duration_is_rejected(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """durationMin сверх суток → 422 (иначе карточка растягивалась на годы)."""
    resp = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=999999999)
    assert resp.status_code == 422, resp.text


async def test_absurd_duration_is_rejected_even_without_doctor(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Без врача проверок занятости нет — граница длительности обязана ловить сама."""
    resp = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=999999999, doctor=None)
    assert resp.status_code == 422, resp.text


async def test_duration_boundary_is_one_day(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """Граница le=1440 включающая: 1440 принимается схемой, 1441 — нет."""
    over = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=1441, doctor=None)
    assert over.status_code == 422, over.text
    exact = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=1440, doctor=None)
    assert exact.status_code == 201, exact.text


async def test_absurd_duration_is_rejected_on_update(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Та же граница на PATCH."""
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30, doctor=None)
    resp = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=clinic_a.headers,
        json={"durationMin": 999999999},
    )
    assert resp.status_code == 422, resp.text


async def test_clearing_service_keeps_duration(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """serviceId: null снимает услугу, но НЕ схлопывает длительность 90 → 30."""
    service90 = await _add_service(client, clinic_a.headers, "Имплантация", 90)
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), service_id=service90)
    assert booked.status_code == 201, booked.text
    detail = await client.get(
        f"{API}/clinic/appointments/{booked.json()['id']}", headers=clinic_a.headers
    )
    assert detail.json()["durationMin"] == 90

    resp = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=clinic_a.headers,
        json={"serviceId": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["serviceId"] is None
    assert resp.json()["durationMin"] == 90, "снятие услуги не должно менять длительность"


async def test_switching_service_still_updates_duration(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Смена услуги без явного durationMin по-прежнему подтягивает новую длительность."""
    service90 = await _add_service(client, clinic_a.headers, "Имплантация 2", 90)
    booked = await _book(
        client, clinic_a, start=at(MONDAY, "09:00"), service_id=clinic_a.service45_id
    )
    assert booked.status_code == 201, booked.text

    resp = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=clinic_a.headers,
        json={"serviceId": service90},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["durationMin"] == 90


@pytest.mark.parametrize("field", ["scheduled_at", "duration_min", "appointment_type", "status"])
async def test_patch_null_via_snake_case_name_is_also_rejected(
    client: AsyncClient, clinic_a: ClinicCtx, field: str
) -> None:
    """Обход запрета через snake_case закрыт.

    CamelModel принимает и camelCase, и snake_case (populate_by_name=True), поэтому
    проверка только по camelCase оставила бы дыру открытой: {"scheduled_at": null}
    прошёл бы мимо валидатора ровно к тому же setattr.
    """
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30)
    assert booked.status_code == 201, booked.text

    resp = await client.patch(
        f"{API}/clinic/appointments/{booked.json()['id']}",
        headers=clinic_a.headers,
        json={field: None},
    )
    assert resp.status_code == 422, f"{field}: {resp.text}"

    alive = await client.get(
        f"{API}/clinic/appointments/{booked.json()['id']}", headers=clinic_a.headers
    )
    assert alive.status_code == 200, alive.text


@pytest.mark.parametrize("field", ["opensAt", "closesAt", "isWorking", "weekday"])
async def test_working_hours_null_field_is_rejected(
    client: AsyncClient, clinic_a: ClinicCtx, field: str
) -> None:
    """Тот же класс в расписании: null в обязательное поле часов клиники → 422."""
    payload = hours_payload()
    payload["days"][0][field] = None
    resp = await client.put(
        f"{API}/clinic/schedule/working-hours", headers=clinic_a.headers, json=payload
    )
    assert resp.status_code == 422, f"{field}: {resp.text}"


@pytest.mark.parametrize("field", ["startsAt", "endsAt", "isWorking", "weekday"])
async def test_doctor_hours_null_field_is_rejected(
    client: AsyncClient, clinic_a: ClinicCtx, field: str
) -> None:
    """null в обязательное поле графика врача → 422."""
    payload = doctor_hours_payload(starts="09:00:00", ends="12:00:00")
    payload["days"][0][field] = None
    resp = await client.put(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/hours",
        headers=clinic_a.headers,
        json=payload,
    )
    assert resp.status_code == 422, f"{field}: {resp.text}"


# ── 13. Отсутствия врача: CRUD ──────────────────────────────────────────────


def time_off_body(*, starts: str, ends: str, reason: str | None = "Отпуск") -> dict[str, Any]:
    """Тело POST time-off: aware-ISO начала/конца в таймзоне клиники."""
    body: dict[str, Any] = {"startsAt": starts, "endsAt": ends}
    if reason is not None:
        body["reason"] = reason
    return body


async def test_time_off_create_list_delete_roundtrip(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Владелец (manage_all): завёл отсутствие → оно в списке → снял → список пуст."""
    base = f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/time-off"

    created = await client.post(
        base,
        headers=clinic_a.headers,
        json=time_off_body(starts=at(MONDAY, "09:00"), ends=at(MONDAY, "13:00")),
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["membershipId"] == clinic_a.doctor_id
    assert item["reason"] == "Отпуск"
    time_off_id = item["id"]

    listed = await client.get(base, headers=clinic_a.headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["timezone"] == "Asia/Bishkek"
    assert [x["id"] for x in listed.json()["items"]] == [time_off_id]

    deleted = await client.delete(f"{base}/{time_off_id}", headers=clinic_a.headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"id": time_off_id, "deleted": True}

    assert (await client.get(base, headers=clinic_a.headers)).json()["items"] == []


async def test_created_time_off_removes_slots(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Отсутствие, заведённое через API, сразу убирает слоты (интеграция с доступностью)."""
    full_day = time_off_body(starts=at(MONDAY, "00:00"), ends=at(TUESDAY, "00:00"), reason=None)
    created = await client.post(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/time-off",
        headers=clinic_a.headers,
        json=full_day,
    )
    assert created.status_code == 201, created.text

    slots = await client.get(
        f"{API}/clinic/schedule/slots?date={MONDAY}&doctorId={clinic_a.doctor_id}",
        headers=clinic_a.headers,
    )
    assert slots.status_code == 200, slots.text
    assert slots.json()["slots"] == []


async def test_time_off_end_before_start_is_422(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Конец не позже начала → 422, запись не создаётся."""
    resp = await client.post(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/time-off",
        headers=clinic_a.headers,
        json=time_off_body(starts=at(MONDAY, "13:00"), ends=at(MONDAY, "09:00")),
    )
    assert resp.status_code == 422, resp.text


async def test_time_off_naive_datetime_is_422(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Начало/конец без таймзоны (naive) → 422, а не тихое сохранение в UTC."""
    resp = await client.post(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/time-off",
        headers=clinic_a.headers,
        json={"startsAt": "2026-07-20T09:00:00", "endsAt": "2026-07-20T13:00:00"},
    )
    assert resp.status_code == 422, resp.text


async def test_time_off_for_doctor_of_other_clinic_denies(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Отсутствие врачу ЧУЖОЙ клиники не заводится и не читается."""
    created = await client.post(
        f"{API}/clinic/schedule/doctors/{clinic_b.doctor_id}/time-off",
        headers=clinic_a.headers,
        json=time_off_body(starts=at(MONDAY, "09:00"), ends=at(MONDAY, "13:00")),
    )
    assert created.status_code == 404, created.text
    assert created.json()["error"] == "doctor_not_found"

    listed = await client.get(
        f"{API}/clinic/schedule/doctors/{clinic_b.doctor_id}/time-off", headers=clinic_a.headers
    )
    assert listed.status_code == 404, listed.text


async def test_time_off_delete_cross_clinic_is_404_and_keeps_record(
    client: AsyncClient, clinic_a: ClinicCtx, clinic_b: ClinicCtx
) -> None:
    """Чужую запись отсутствия из другой клиники удалить нельзя (404), она остаётся."""
    base_b = f"{API}/clinic/schedule/doctors/{clinic_b.doctor_id}/time-off"
    created = await client.post(
        base_b,
        headers=clinic_b.headers,
        json=time_off_body(starts=at(MONDAY, "09:00"), ends=at(MONDAY, "13:00")),
    )
    assert created.status_code == 201, created.text
    time_off_id = created.json()["id"]

    # Владелец A пытается снять отсутствие врача B по его же id — 404 (скоуп клиники).
    attack = await client.delete(
        f"{API}/clinic/schedule/doctors/{clinic_b.doctor_id}/time-off/{time_off_id}",
        headers=clinic_a.headers,
    )
    assert attack.status_code == 404, attack.text

    still_there = await client.get(base_b, headers=clinic_b.headers)
    assert [x["id"] for x in still_there.json()["items"]] == [time_off_id]


async def test_time_off_delete_unknown_id_is_404(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Удаление несуществующего отсутствия → 404 time_off_not_found."""
    resp = await client.delete(
        f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/time-off/{uuid.uuid4()}",
        headers=clinic_a.headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"] == "time_off_not_found"


async def test_worker_manages_own_time_off_not_others(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Работник (manage_own/view_own): CRUD своих отсутствий; чужие — 403."""
    own_id, phone = await _add_doctor(client, clinic_a.headers, "Работник-отпускник")
    worker = await otp_login(client, phone)
    own_base = f"{API}/clinic/schedule/doctors/{own_id}/time-off"
    other_base = f"{API}/clinic/schedule/doctors/{clinic_a.doctor_id}/time-off"

    # Своё отсутствие — заводится, видно, снимается.
    created = await client.post(
        own_base,
        headers=worker,
        json=time_off_body(starts=at(MONDAY, "09:00"), ends=at(MONDAY, "13:00")),
    )
    assert created.status_code == 201, created.text
    time_off_id = created.json()["id"]
    assert [x["id"] for x in (await client.get(own_base, headers=worker)).json()["items"]] == [
        time_off_id
    ]

    # Чужие отсутствия — 403 на создание, чтение и удаление.
    other_create = await client.post(
        other_base,
        headers=worker,
        json=time_off_body(starts=at(MONDAY, "09:00"), ends=at(MONDAY, "13:00")),
    )
    assert other_create.status_code == 403, other_create.text
    assert other_create.json()["error"] == "not_own_schedule"
    assert (await client.get(other_base, headers=worker)).status_code == 403
    assert (
        await client.delete(f"{other_base}/{uuid.uuid4()}", headers=worker)
    ).status_code == 403

    deleted = await client.delete(f"{own_base}/{time_off_id}", headers=worker)
    assert deleted.status_code == 200, deleted.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", f"/clinic/schedule/doctors/{uuid.uuid4()}/time-off"),
        ("POST", f"/clinic/schedule/doctors/{uuid.uuid4()}/time-off"),
        ("DELETE", f"/clinic/schedule/doctors/{uuid.uuid4()}/time-off/{uuid.uuid4()}"),
    ],
)
async def test_time_off_endpoints_without_token_return_401(
    client: AsyncClient, method: str, path: str
) -> None:
    """Ручки отсутствий закрыты авторизацией."""
    resp = await client.request(method, f"{API}{path}", json={})
    assert resp.status_code == 401, resp.text


# ── 11. Запись вне графика (force) ──────────────────────────────────────────
#
# Клиники принимают вне графика постоянно: жёсткий запрет приводит не к порядку,
# а к тому, что приём уедет в тетрадь. Поэтому право снимает проверку рабочих
# часов — и только её: пересечение приёмов и отпуск врача не обходятся никогда.


async def test_force_books_outside_working_hours(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Воскресенье — выходной клиники, но с правом и флагом запись проходит."""
    rejected = await _book(client, clinic_a, start=at(SUNDAY, "10:00"), duration=30)
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"] == "outside_working_hours"

    forced = await _book(client, clinic_a, start=at(SUNDAY, "10:00"), duration=30, force=True)
    assert forced.status_code == 201, forced.text


async def test_force_does_not_bypass_overlap(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """Двойная запись остаётся запрещённой: флаг про часы, а не про занятость."""
    booked = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30)
    assert booked.status_code == 201, booked.text

    forced = await _book(client, clinic_a, start=at(MONDAY, "09:00"), duration=30, force=True)
    assert forced.status_code == 409, forced.text
    assert forced.json()["error"] == "slot_taken"


async def test_force_does_not_bypass_time_off(
    _resources: Resources, client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Отпуск врача флагом не обходится — врача в клинике попросту нет."""
    await _add_time_off(
        _resources, clinic_a.clinic_id, clinic_a.doctor_id, at(MONDAY, "09:00"), at(MONDAY, "11:00")
    )
    forced = await _book(client, clinic_a, start=at(MONDAY, "10:00"), duration=30, force=True)
    assert forced.status_code == 422, forced.text
    assert forced.json()["error"] == "doctor_time_off"


async def test_force_needs_permission(client: AsyncClient, clinic_a: ClinicCtx) -> None:
    """У врача есть appointments.create, но записывать вне графика он не может."""
    membership_id, phone = await _add_doctor(client, clinic_a.headers, "Врач Форс")
    worker = await otp_login(client, phone)

    resp = await _book(
        client,
        clinic_a,
        start=at(SUNDAY, "10:00"),
        duration=30,
        doctor=membership_id,
        force=True,
        headers=worker,
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "force_booking_forbidden"


async def test_force_moves_appointment_outside_hours(
    client: AsyncClient, clinic_a: ClinicCtx
) -> None:
    """Перенос вне графика — та же операция: без флага 422, с флагом проходит."""
    booked = await _book(client, clinic_a, start=at(MONDAY, "10:00"), duration=30)
    assert booked.status_code == 201, booked.text
    appointment_id = booked.json()["id"]

    rejected = await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=clinic_a.headers,
        json={"scheduledAt": at(SUNDAY, "10:00")},
    )
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"] == "outside_working_hours"

    forced = await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=clinic_a.headers,
        json={"scheduledAt": at(SUNDAY, "10:00"), "force": True},
    )
    assert forced.status_code == 200, forced.text
