"""Пакетный подбор свободного времени ассистента: без N+1 и с прежним результатом.

``BookingService.free_days`` (инструмент ``get_available_days``) и
``earliest_slots`` (шаблоны медфильтра) раньше перебирали всех врачей клиники
× до 7 дней и на каждую пару звали ``GetSlotsUseCase.execute`` — шесть-восемь
запросов на пару, сотни на один вызов инструмента. Теперь часы клиники,
графики, отсутствия и занятость всех врачей поднимаются на период разом, а
слоты считаются в памяти тем же правилом (``doctor_free_time``). Здесь:

* результат совпадает с прежним алгоритмом — он воспроизведён ниже дословно
  поверх того же ``execute`` по каждой паре «врач × день» — на нескольких
  врачах: свой график, отсутствие на день и на часть дня, приёмы (в т.ч.
  начавшийся накануне и не выровненный по сетке), отменённый приём, услуга с
  назначенными врачами, чужой врач;
* пакетный ``execute_days`` отдаёт ровно те же слоты, что ``execute`` по дням;
* число SQL-запросов не зависит ни от числа врачей, ни от числа дней.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, Self
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AppError, ValidationAppError
from app.modules.ai.application import booking
from app.modules.ai.application.booking import BookingService, earliest_bookable
from app.modules.schedule.application.services.availability_service import (
    build_availability_service,
)
from app.modules.schedule.application.use_cases.get_slots import GetSlotsUseCase
from app.modules.schedule.infrastructure.repositories.schedule_repository import (
    ScheduleRepository,
)
from app.shared.db.enums import AppointmentStatus
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.membership import Membership
from tests.conftest import API, Resources, otp_login

_BISHKEK = ZoneInfo("Asia/Bishkek")
_seq = itertools.count(1)


def _phone() -> str:
    return f"070084{next(_seq):04d}"


def _next_monday() -> date:
    """Ближайший понедельник строго после сегодняшнего дня клиники — неделя целиком впереди."""
    today = datetime.now(_BISHKEK).date()
    return today + timedelta(days=7 - today.weekday())


def _at(day: date, hhmm: str) -> datetime:
    """Местное время клиники в этот день."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=_BISHKEK)


# ── Клиника для сравнения ───────────────────────────────────────────────────


@dataclass(slots=True)
class Clinic:
    """Клиника с четырьмя врачами разной загрузки на неделю с понедельника ``monday``."""

    clinic_id: uuid.UUID
    headers: dict[str, str]
    monday: date
    #: Альфа — по часам клиники; Бета — свой график 14–21; Гамма — отсутствия;
    #: Дельта — приём с вечера воскресенья и полностью занятая пятница.
    doctors: list[uuid.UUID]
    #: Услуга на 45 минут, назначенная только Бете и Гамме.
    service_id: uuid.UUID
    patient_id: str


async def _provision(admin_client: AsyncClient, client: AsyncClient) -> tuple[str, dict[str, str]]:
    """Завести клинику через админку и войти владельцем."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    resp = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Пакета",
            "adminPhone": phone,
            "name": "Клиника Пакетная",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 20,
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"]), await otp_login(client, phone)


async def _add_doctor(client: AsyncClient, headers: dict[str, str], name: str) -> uuid.UUID:
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    resp = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": name, "phone": _phone(), "specializationId": spec},
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["membershipId"])


async def _put(client: AsyncClient, headers: dict[str, str], path: str, body: Any) -> None:
    resp = await client.put(f"{API}{path}", headers=headers, json=body)
    assert resp.status_code == 200, resp.text


async def _post(client: AsyncClient, headers: dict[str, str], path: str, body: Any) -> Any:
    resp = await client.post(f"{API}{path}", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _book(
    client: AsyncClient,
    clinic: Clinic,
    doctor: uuid.UUID,
    start: datetime,
    duration: int,
    *,
    force: bool = False,
) -> str:
    body: dict[str, Any] = {
        "patientId": clinic.patient_id,
        "doctorMembershipId": str(doctor),
        "scheduledAt": start.isoformat(),
        "durationMin": duration,
    }
    if force:
        body["force"] = True
    return str((await _post(client, clinic.headers, "/clinic/appointments", body))["id"])


@pytest.fixture
async def clinic(admin_client: AsyncClient, client: AsyncClient, _resources: Resources) -> Clinic:
    clinic_id, headers = await _provision(admin_client, client)
    # Пн–пт 08–20 с обедом 13–14, сб 10–15 без обеда, вс — выходной.
    await _put(
        client,
        headers,
        "/clinic/schedule/working-hours",
        {
            "days": [
                {
                    "weekday": d,
                    "opensAt": "10:00:00" if d == 5 else "08:00:00",
                    "closesAt": "15:00:00" if d == 5 else "20:00:00",
                    "breakStart": None if d == 5 else "13:00:00",
                    "breakEnd": None if d == 5 else "14:00:00",
                    "isWorking": d != 6,
                }
                for d in range(7)
            ]
        },
    )
    alpha = await _add_doctor(client, headers, "Врач Альфа")
    beta = await _add_doctor(client, headers, "Врач Бета")
    gamma = await _add_doctor(client, headers, "Врач Гамма")
    delta = await _add_doctor(client, headers, "Врач Дельта")
    # Свой график Беты выходит за закрытие клиники: окно — пересечение, 14–20.
    await _put(
        client,
        headers,
        f"/clinic/schedule/doctors/{beta}/hours",
        {
            "days": [
                {"weekday": d, "startsAt": "14:00:00", "endsAt": "21:00:00", "isWorking": d < 5}
                for d in range(7)
            ]
        },
    )
    service = await _post(
        client,
        headers,
        "/clinic/services",
        {
            "name": "Лечение 45",
            "priceTiyin": 1000,
            "duration": 45,
            "doctorIds": [str(beta), str(gamma)],
        },
    )
    patient = await _post(
        client,
        headers,
        "/clinic/patients",
        {"phone": _phone(), "firstName": "Пакет", "lastName": "Тестовый"},
    )
    ctx = Clinic(
        clinic_id=uuid.UUID(clinic_id),
        headers=headers,
        monday=_next_monday(),
        doctors=[alpha, beta, gamma, delta],
        service_id=uuid.UUID(service["id"]),
        patient_id=str(patient["id"]),
    )
    mon = ctx.monday
    tue, wed, thu, fri = (mon + timedelta(days=n) for n in range(1, 5))

    # Альфа: утро понедельника, приём с концом не по сетке (10:10–11:00) и
    # отменённый приём во вторник — он слот не занимает.
    await _book(client, ctx, alpha, _at(mon, "08:00"), 60)
    await _book(client, ctx, alpha, _at(mon, "10:10"), 50)
    cancelled = await _book(client, ctx, alpha, _at(tue, "15:00"), 30)
    async with _resources.factory() as session:
        await session.execute(
            update(Appointment)
            .where(Appointment.id == uuid.UUID(cancelled))
            .values(status=AppointmentStatus.CANCELLED)
        )
        await session.commit()
    # Бета: длинный приём в среду.
    await _book(client, ctx, beta, _at(wed, "16:00"), 90)
    # Гамма: приём в понедельник, весь вторник и часть четверга — отсутствие.
    await _book(client, ctx, gamma, _at(mon, "09:00"), 45)
    for starts, ends in (
        (_at(tue, "00:00"), _at(wed, "00:00")),
        (_at(thu, "10:00"), _at(thu, "12:30")),
    ):
        await _post(
            client,
            headers,
            f"/clinic/schedule/doctors/{gamma}/time-off",
            {"startsAt": starts.isoformat(), "endsAt": ends.isoformat(), "reason": "Отпуск"},
        )
    # Дельта: приём начался в воскресенье вечером и закончился в понедельник
    # 09:30 (начало ДО периода подбора), пятница занята целиком.
    await _book(client, ctx, delta, _at(mon - timedelta(days=1), "22:00"), 690, force=True)
    await _book(client, ctx, delta, _at(fri, "08:00"), 300)
    await _book(client, ctx, delta, _at(fri, "14:00"), 360)
    return ctx


# ── Прежний алгоритм — дословно, как эталон ────────────────────────────────


def _use_case(session: AsyncSession) -> GetSlotsUseCase:
    return GetSlotsUseCase(ScheduleRepository(session), build_availability_service(session))


async def _legacy_doctors(
    session: AsyncSession,
    clinic_id: uuid.UUID,
    doctor_membership_id: uuid.UUID | None,
    service_id: uuid.UUID | None,
) -> list[uuid.UUID | None]:
    """Прежний ``BookingService._doctors_to_search``."""
    if doctor_membership_id is not None or service_id is not None:
        return [doctor_membership_id]
    rows = (
        await session.execute(
            select(Membership.id).where(
                Membership.clinic_id == clinic_id,
                Membership.active.is_(True),
                Membership.specialization_id.is_not(None),
            )
        )
    ).scalars()
    return list(rows)


async def _legacy_day_starts(
    use_case: GetSlotsUseCase,
    clinic_id: uuid.UUID,
    doctors: list[uuid.UUID | None],
    day: date,
    service_id: uuid.UUID | None,
    *,
    not_before: datetime,
) -> list[datetime]:
    """Прежний ``BookingService._day_starts``: ``execute`` на каждого врача."""
    starts: set[datetime] = set()
    for doctor_id in doctors:
        try:
            plan = await use_case.execute(clinic_id, doctor_id, day, service_id)
        except AppError:
            continue
        starts.update(slot.span.start for slot in plan.slots if slot.span.start >= not_before)
    return sorted(starts)


async def _legacy_free_days(
    session: AsyncSession,
    clinic_id: uuid.UUID,
    *,
    now: datetime,
    date_from: datetime,
    days: int,
    doctor_membership_id: uuid.UUID | None,
    service_id: uuid.UUID | None,
) -> list[dict[str, Any]]:
    """Прежний ``BookingService.free_days`` (часы — явным ``now``)."""
    use_case = _use_case(session)
    not_before = earliest_bookable(now)
    doctors = await _legacy_doctors(session, clinic_id, doctor_membership_id, service_id)
    result: list[dict[str, Any]] = []
    day = max(date_from, now).astimezone(_BISHKEK).date()
    for offset in range(min(days, 7)):
        current = day + timedelta(days=offset)
        starts = await _legacy_day_starts(
            use_case, clinic_id, doctors, current, service_id, not_before=not_before
        )
        if starts:
            result.append(
                {
                    "date": current.isoformat(),
                    "freeSlots": len(starts),
                    "firstSlot": starts[0].astimezone(_BISHKEK).strftime("%H:%M"),
                }
            )
    return result


async def _legacy_earliest_slots(
    session: AsyncSession, clinic_id: uuid.UUID, *, now: datetime, days: int, limit: int
) -> list[datetime]:
    """Прежний ``BookingService.earliest_slots`` (часы — явным ``now``)."""
    use_case = _use_case(session)
    not_before = earliest_bookable(now)
    doctors = await _legacy_doctors(session, clinic_id, None, None)
    today = now.astimezone(_BISHKEK).date()
    for offset in range(days):
        starts = await _legacy_day_starts(
            use_case,
            clinic_id,
            doctors,
            today + timedelta(days=offset),
            None,
            not_before=not_before,
        )
        if starts:
            return [start.astimezone(_BISHKEK) for start in starts[:limit]]
    return []


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Остановить часы модуля записи: его «сейчас» — ``moment``.

    Эталон и новый код зовутся в разные мгновения; без заморозки граница
    ``AI_SLOT_LEAD_MINUTES`` могла бы перешагнуть точку сетки между вызовами.
    """

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(moment.timestamp(), tz)

    monkeypatch.setattr(booking, "datetime", _Frozen)


# ── 1. Тот же результат ─────────────────────────────────────────────────────


async def test_free_days_match_legacy_algorithm(
    clinic: Clinic, _resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Все режимы подбора дают ровно то, что давал перебор «врач × день»."""
    alpha, beta, gamma, _ = clinic.doctors
    # «Сейчас» — понедельник 10:07: сегодняшнее утро отсекается запасом времени.
    now = _at(clinic.monday, "10:07")
    _freeze(monkeypatch, now)
    modes: dict[str, tuple[uuid.UUID | None, uuid.UUID | None]] = {
        "все врачи": (None, None),
        "врач по часам клиники": (alpha, None),
        "врач со своим графиком": (beta, None),
        "врач с отсутствиями": (gamma, None),
        "услуга без врача": (None, clinic.service_id),
        "врач и услуга": (gamma, clinic.service_id),
        "врач без услуги-исполнителя": (alpha, clinic.service_id),
        "чужой врач": (uuid.uuid4(), None),
        "чужая услуга": (None, uuid.uuid4()),
    }
    for label, (doctor_id, service_id) in modes.items():
        for date_from, days in ((now, 7), (now + timedelta(days=2), 3), (now, 1)):
            async with _resources.factory() as session:
                expected = await _legacy_free_days(
                    session,
                    clinic.clinic_id,
                    now=now,
                    date_from=date_from,
                    days=days,
                    doctor_membership_id=doctor_id,
                    service_id=service_id,
                )
            async with _resources.factory() as session:
                actual = await BookingService(session).free_days(
                    clinic.clinic_id,
                    date_from=date_from,
                    days=days,
                    doctor_membership_id=doctor_id,
                    service_id=service_id,
                )
            assert actual == expected, f"{label}, с {date_from:%a %H:%M} на {days} дн."

    # Сверка не пустая: в общем режиме есть все рабочие дни недели, кроме воскресенья.
    async with _resources.factory() as session:
        week = await BookingService(session).free_days(
            clinic.clinic_id,
            date_from=now,
            days=7,
            doctor_membership_id=None,
            service_id=None,
        )
    assert [d["date"] for d in week] == [
        (clinic.monday + timedelta(days=n)).isoformat() for n in range(6)
    ]
    assert week[0]["firstSlot"] == "10:45"  # 10:07 + 30 мин запаса → ближайшая точка сетки


@pytest.mark.parametrize(
    ("day_offset", "hhmm", "limit"),
    [
        (0, "10:07", 3),  # понедельник: сегодня, после запаса
        (0, "19:40", 5),  # понедельник вечером: сегодня уже поздно — вторник
        (5, "16:00", 3),  # суббота после закрытия, воскресенье выходной — понедельник
        (4, "07:00", 10),  # пятница: Дельта занята, остальные свободны
    ],
)
async def test_earliest_slots_match_legacy_algorithm(
    clinic: Clinic,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
    day_offset: int,
    hhmm: str,
    limit: int,
) -> None:
    """Шаблоны медфильтра: те же самые ранние начала первого дня со временем."""
    now = _at(clinic.monday + timedelta(days=day_offset), hhmm)
    _freeze(monkeypatch, now)
    async with _resources.factory() as session:
        expected = await _legacy_earliest_slots(
            session, clinic.clinic_id, now=now, days=3, limit=limit
        )
    async with _resources.factory() as session:
        actual = await BookingService(session).earliest_slots(clinic.clinic_id, limit=limit)

    assert expected, "эталон пуст — сверка ничего не проверяет"
    assert actual == expected
    assert all(start.tzinfo == _BISHKEK for start in actual)  # время клиники, не UTC


async def test_execute_days_equals_execute_day_by_day(
    clinic: Clinic, _resources: Resources
) -> None:
    """Пакетный use-case отдаёт те же слоты (с врачами и порядком), что по дню за раз."""
    days = [clinic.monday + timedelta(days=n) for n in range(7)]
    targets: list[tuple[uuid.UUID | None, uuid.UUID | None]] = [
        *((doctor, None) for doctor in clinic.doctors),
        (None, clinic.service_id),
        (clinic.doctors[2], clinic.service_id),
    ]
    async with _resources.factory() as session:
        use_case = _use_case(session)
        for doctor_id, service_id in targets:
            batch = await use_case.execute_days(clinic.clinic_id, doctor_id, days, service_id)
            one_by_one = [
                await use_case.execute(clinic.clinic_id, doctor_id, day, service_id) for day in days
            ]
            assert batch == one_by_one, (doctor_id, service_id)

        # Без врача и услуги — все врачи: сетка = слоты каждого врача, слитые
        # по времени (при равном времени — в порядке врачей по имени).
        everyone = await use_case.execute_days(clinic.clinic_id, None, days, None, any_doctor=True)
        practitioners = await ScheduleRepository(session).practitioner_ids(clinic.clinic_id)
        assert set(practitioners) == set(clinic.doctors)
        for day, plan in zip(days, everyone, strict=True):
            per_doctor = [
                slot
                for doctor in practitioners
                for slot in (await use_case.execute(clinic.clinic_id, doctor, day, None)).slots
            ]
            assert plan.slots == sorted(per_doctor, key=lambda s: (s.span.start, s.span.end))
            assert plan.duration_min == 30


async def test_execute_days_keeps_doctor_or_service_rule(
    clinic: Clinic, _resources: Resources
) -> None:
    """Без явного ``any_doctor`` правило «нужен врач или услуга» прежнее."""
    async with _resources.factory() as session:
        with pytest.raises(ValidationAppError) as raised:
            await _use_case(session).execute_days(clinic.clinic_id, None, [clinic.monday], None)
    assert raised.value.code == "doctor_or_service_required"


# ── 2. Число запросов ───────────────────────────────────────────────────────


async def _count_queries(res: Resources, call: Callable[[AsyncSession], Awaitable[Any]]) -> int:
    """Сколько SQL-запросов ушло в БД за ``call`` (в свежей сессии)."""
    engine: AsyncEngine = res.factory.kw["bind"]
    executed = 0

    def _on_execute(*_: Any) -> None:
        nonlocal executed
        executed += 1

    async with res.factory() as session:
        event.listen(engine.sync_engine, "before_cursor_execute", _on_execute)
        try:
            await call(session)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _on_execute)
    return executed


async def test_query_count_does_not_grow_with_doctors_or_days(
    admin_client: AsyncClient, client: AsyncClient, _resources: Resources
) -> None:
    """Неделя на восемь врачей — столько же запросов, сколько день на двоих."""
    clinic_id, headers = await _provision(admin_client, client)
    clinic = uuid.UUID(clinic_id)
    monday = _next_monday()
    date_from = _at(monday, "00:00")
    for n in range(2):
        await _add_doctor(client, headers, f"Врач {n}")

    async def free_days(days: int) -> Callable[[AsyncSession], Awaitable[Any]]:
        async def call(session: AsyncSession) -> Any:
            return await BookingService(session).free_days(
                clinic,
                date_from=date_from,
                days=days,
                doctor_membership_id=None,
                service_id=None,
            )

        return call

    async def earliest(session: AsyncSession) -> Any:
        return await BookingService(session).earliest_slots(clinic)

    two_doctors_day = await _count_queries(_resources, await free_days(1))
    two_doctors_week = await _count_queries(_resources, await free_days(7))
    two_doctors_earliest = await _count_queries(_resources, earliest)

    for n in range(2, 8):
        await _add_doctor(client, headers, f"Врач {n}")
    eight_doctors_week = await _count_queries(_resources, await free_days(7))
    eight_doctors_earliest = await _count_queries(_resources, earliest)

    # Таймзона, клиника, список врачей, часы клиники, графики, отсутствия, занятость.
    assert two_doctors_day == two_doctors_week == eight_doctors_week == 7
    assert two_doctors_earliest == eight_doctors_earliest == 7

    # Для сравнения — прежний перебор: запросы на каждую пару «врач × день».
    async def legacy(session: AsyncSession) -> Any:
        now = datetime.now(UTC)
        return await _legacy_free_days(
            session,
            clinic,
            now=now,
            date_from=date_from,
            days=7,
            doctor_membership_id=None,
            service_id=None,
        )

    assert await _count_queries(_resources, legacy) > 8 * 7
