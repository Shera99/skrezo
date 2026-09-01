"""API-тесты длительности приёма: время услуги задано НА ОДИН ЗУБ.

Клиника заводит услугу «лечение кариеса, 40 минут» — это время одного зуба.
Пока длительность бралась только из ``serviceId`` приёма, запись на три зуба
занимала слот одного: врач лечил два часа, а расписание показывало сорок минут
и пускало в кресло следующего пациента. Отсюда проверки ниже:

* позиция на несколько зубов растягивает приём кратно числу зубов;
* позиции суммируются — приём идут столько, сколько работ в чеке;
* ``quantity`` и зубы не перемножаются: заполнивший оба регистратор не должен
  получить приём вчетверо длиннее;
* явный ``durationMin`` сильнее любой арифметики;
* считать не по чему — работает прежнее правило (услуга приёма, иначе 30);
* слоты нарезаются под то же время, иначе выбранный слот не примет запись.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, otp_login, own_membership_id

_phone_seq = itertools.count(1)
_day_seq = itertools.count()


def _phone() -> str:
    return f"070098{next(_phone_seq):04d}"


def _workday(offset: int) -> date:
    day = date.today() + timedelta(days=1 + offset)
    if day.weekday() == 6:  # воскресенье — выходной по умолчанию
        day += timedelta(days=1)
    return day


def _morning_slot() -> str:
    """09:00 нового дня: от него до обеда помещается приём до трёх часов.

    Общий ``working_slot()`` раздаёт часы вплотную к обеду (12:00–13:00), и
    двухчасовой приём из него упирался бы в перерыв — проверка длительности
    превратилась бы в проверку графика.
    """
    return f"{_workday(next(_day_seq)).isoformat()}T09:00:00+06:00"


@dataclass(slots=True)
class ClinicCtx:
    headers: dict[str, str]
    doctor_id: str
    patient_id: str
    filling_id: str  # «Пломба», 40 минут на зуб
    cleaning_id: str  # «Чистка», 20 минут, к зубу не привязана
    priceless_id: str  # «Консультация» без заданной длительности
    implant_id: str  # «Имплантация», 200 минут на зуб — для проверки потолка


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Время",
            "adminPhone": phone,
            "name": "Клиника Время",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(client, phone)

    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Долгий"},
    )
    assert patient.status_code == 201, patient.text

    async def service(name: str, duration: int | None) -> str:
        body: dict[str, Any] = {"name": name, "priceTiyin": 3000}
        if duration is not None:
            body["duration"] = duration
        resp = await client.post(f"{API}/clinic/services", headers=headers, json=body)
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    return ClinicCtx(
        headers=headers,
        doctor_id=await own_membership_id(client, headers),
        patient_id=str(patient.json()["id"]),
        filling_id=await service("Пломба", 40),
        cleaning_id=await service("Чистка", 20),
        priceless_id=await service("Консультация", None),
        implant_id=await service("Имплантация", 200),
    )


async def _book(
    client: AsyncClient, ctx: ClinicCtx, body: dict[str, Any]
) -> Any:
    return await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "doctorMembershipId": ctx.doctor_id,
            "scheduledAt": _morning_slot(),
            **body,
        },
    )


async def _duration(client: AsyncClient, ctx: ClinicCtx, body: dict[str, Any]) -> int:
    resp = await _book(client, ctx, body)
    assert resp.status_code == 201, resp.text
    card = await client.get(
        f"{API}/clinic/appointments/{resp.json()['id']}", headers=ctx.headers
    )
    assert card.status_code == 200, card.text
    return int(card.json()["durationMin"])


# ── 1. Кратность числу зубов ────────────────────────────────────────────────


async def test_single_tooth_keeps_service_duration(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Один зуб — длительность услуги как есть: прежнее поведение не поехало."""
    assert (
        await _duration(client, ctx, {"items": [{"serviceId": ctx.filling_id, "teeth": [36]}]})
        == 40
    )


async def test_duration_scales_with_teeth(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Три зуба одной позицией — втрое дольше: 40 минут заданы на один зуб."""
    assert (
        await _duration(
            client, ctx, {"items": [{"serviceId": ctx.filling_id, "teeth": [36, 37, 38]}]}
        )
        == 120
    )


async def test_repeated_tooth_counted_once(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Зуб, названный в позиции дважды, — один зуб (как и при записи в БД)."""
    assert (
        await _duration(
            client, ctx, {"items": [{"serviceId": ctx.filling_id, "teeth": [36, 36, 37]}]}
        )
        == 80
    )


async def test_items_sum_up(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Позиции складываются: «две пломбы + чистка» = 40×2 + 20."""
    assert (
        await _duration(
            client,
            ctx,
            {
                "items": [
                    {"serviceId": ctx.filling_id, "teeth": [36, 37]},
                    {"serviceId": ctx.cleaning_id},
                ]
            },
        )
        == 100
    )


# ── 2. Зубы против quantity ─────────────────────────────────────────────────


async def test_quantity_counts_when_no_teeth(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Работа вне зуба считается по количеству: «чистка ×3» — это 60 минут."""
    assert (
        await _duration(
            client, ctx, {"items": [{"serviceId": ctx.cleaning_id, "quantity": 3}]}
        )
        == 60
    )


async def test_teeth_win_over_quantity(client: AsyncClient, ctx: ClinicCtx) -> None:
    """quantity и зубы не перемножаются: два зуба при quantity=2 — это ×2, не ×4.

    Регистратор часто дублирует количество и список зубов; перемножение дало бы
    ему приём на 160 минут вместо 80 и съело бы у врача полдня.
    """
    assert (
        await _duration(
            client,
            ctx,
            {"items": [{"serviceId": ctx.filling_id, "teeth": [36, 37], "quantity": 2}]},
        )
        == 80
    )


# ── 3. Приоритеты источников ────────────────────────────────────────────────


async def test_explicit_duration_wins(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Явный durationMin сильнее расчёта: врач вправе сказать «мне хватит 45»."""
    assert (
        await _duration(
            client,
            ctx,
            {
                "durationMin": 45,
                "items": [{"serviceId": ctx.filling_id, "teeth": [36, 37, 38]}],
            },
        )
        == 45
    )


async def test_service_without_duration_falls_back(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """У услуги позиции нет длительности — считать не по чему, работает дефолт."""
    assert (
        await _duration(
            client, ctx, {"items": [{"serviceId": ctx.priceless_id, "teeth": [36, 37]}]}
        )
        == 30
    )


async def test_appointment_service_used_without_items(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Позиций нет — длительность по-прежнему берётся из услуги приёма."""
    assert await _duration(client, ctx, {"serviceId": ctx.filling_id}) == 40


async def test_items_win_over_appointment_service(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Состав важнее serviceId: приём идут столько, сколько работ в чеке."""
    assert (
        await _duration(
            client,
            ctx,
            {
                "serviceId": ctx.cleaning_id,
                "items": [{"serviceId": ctx.filling_id, "teeth": [36, 37]}],
            },
        )
        == 80
    )


# ── 4. Потолок ──────────────────────────────────────────────────────────────


async def test_too_long_composition_rejected(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Состав длиннее суток отвергается, а не обрезается молча.

    Проверка обязана срабатывать РАНЬШЕ графика: иначе врач получил бы
    «нерабочее время» и правил дату, тогда как чинить надо состав.
    """
    teeth = [11, 12, 13, 14, 15, 16, 17, 18]  # 200 минут × 8 = 1600 > суток
    resp = await _book(client, ctx, {"items": [{"serviceId": ctx.implant_id, "teeth": teeth}]})
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"] == "duration_too_long"
    assert body["durationMin"] == 200 * len(teeth)
    assert body["maxDurationMin"] == 24 * 60


# ── 5. Слоты ────────────────────────────────────────────────────────────────


async def test_slots_sized_for_several_teeth(client: AsyncClient, ctx: ClinicCtx) -> None:
    """teethCount растягивает слот: иначе регистратор выберет слот на один зуб."""
    day = _workday(next(_day_seq)).isoformat()
    params = {"date": day, "doctorId": ctx.doctor_id, "serviceId": ctx.filling_id}
    one = await client.get(f"{API}/clinic/schedule/slots", headers=ctx.headers, params=params)
    assert one.status_code == 200, one.text
    assert one.json()["durationMin"] == 40

    three = await client.get(
        f"{API}/clinic/schedule/slots",
        headers=ctx.headers,
        params={**params, "teethCount": 3},
    )
    assert three.status_code == 200, three.text
    assert three.json()["durationMin"] == 120
    # Слот на два часа помещается в день реже, чем сорокаминутный.
    assert len(three.json()["slots"]) < len(one.json()["slots"])
