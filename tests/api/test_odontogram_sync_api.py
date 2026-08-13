"""API-тесты синхронизации одонтограммы с завершённым приёмом.

Карта зуба показывает ТЕКУЩЕЕ состояние, история — что делали. Раньше они жили
раздельно: вылечили кариес на 36 — в карте он так и оставался кариозным, пока
кто-то не поправит вручную. Отсюда риски:

* исход берётся из настройки услуги (`toothOutcome`), а не угадывается по её
  названию — прайс ведёт клиника, и «Реставрация II класса» ничему не подскажет;
* услуги без исхода (чистка, снимок) и отменённые позиции карту не трогают;
* если в одном приёме зуб сначала запломбировали, а потом удалили, побеждает
  последняя работа — иначе состояние зависело бы от плана запроса;
* одонтограмма общая на платформу: клиника B видит результат лечения в клинике A.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import API, otp_login, own_membership_id, working_slot

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070088{next(_phone_seq):04d}"


@dataclass(slots=True)
class ClinicCtx:
    headers: dict[str, str]
    doctor_id: str  # своё членство владельца: врач у приёма обязателен
    patient_id: str
    filling_id: str  # «Пломба» → filling
    extraction_id: str  # «Удаление» → missing
    cleaning_id: str  # «Чистка» → исхода нет


async def _service(
    client: AsyncClient, headers: dict[str, str], name: str, outcome: str | None
) -> str:
    body: dict[str, Any] = {"name": name, "priceTiyin": 3000}
    if outcome is not None:
        body["toothOutcome"] = outcome
    resp = await client.post(f"{API}/clinic/services", headers=headers, json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["toothOutcome"] == outcome
    return str(resp.json()["id"])


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> ClinicCtx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Карта",
            "adminPhone": phone,
            "name": "Клиника Карта",
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
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Зубов"},
    )
    return ClinicCtx(
        headers=headers,
        doctor_id=await own_membership_id(client, headers),
        patient_id=str(patient.json()["id"]),
        filling_id=await _service(client, headers, "Пломба", "filling"),
        extraction_id=await _service(client, headers, "Удаление", "missing"),
        cleaning_id=await _service(client, headers, "Чистка", None),
    )


async def _book(client: AsyncClient, ctx: ClinicCtx, items: list[dict[str, Any]]) -> str:
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.headers,
        json={
            "patientId": ctx.patient_id,
            "doctorMembershipId": ctx.doctor_id,
            "scheduledAt": working_slot(),
            "items": items,
        },
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _odontogram(client: AsyncClient, ctx: ClinicCtx) -> dict[int, str]:
    resp = await client.get(f"{API}/clinic/patients/{ctx.patient_id}", headers=ctx.headers)
    assert resp.status_code == 200, resp.text
    return {t["tooth"]: t["status"] for t in resp.json()["odontogram"]}


async def _set_tooth(client: AsyncClient, ctx: ClinicCtx, tooth: int, status: str) -> None:
    resp = await client.put(
        f"{API}/clinic/patients/{ctx.patient_id}/teeth",
        headers=ctx.headers,
        json={"teeth": [{"tooth": tooth, "status": status}]},
    )
    assert resp.status_code == 200, resp.text


# ── 1. Основной сценарий ────────────────────────────────────────────────────


async def test_completed_treatment_updates_tooth(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Вылечили кариес — карта перестаёт показывать больной зуб."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [36]}])
    await _set_tooth(client, ctx, 36, "caries")
    assert (await _odontogram(client, ctx))[36] == "caries"

    done = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers
    )
    assert done.status_code == 200, done.text
    assert (await _odontogram(client, ctx))[36] == "filling"


async def test_sync_happens_on_patch_completion_too(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Закрытие через PATCH — тот же факт, карта обновляется одинаково."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.extraction_id, "teeth": [17]}])
    resp = await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=ctx.headers,
        json={"status": "completed"},
    )
    assert resp.status_code == 200, resp.text
    assert (await _odontogram(client, ctx))[17] == "missing"


async def test_item_with_several_teeth_updates_each(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Одна работа на несколько зубов меняет состояние каждого."""
    appointment_id = await _book(
        client, ctx, [{"serviceId": ctx.filling_id, "teeth": [35, 36, 37]}]
    )
    await client.post(f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers)

    odontogram = await _odontogram(client, ctx)
    assert [odontogram[t] for t in (35, 36, 37)] == ["filling"] * 3


# ── 2. Чего синк НЕ делает ──────────────────────────────────────────────────


async def test_service_without_outcome_leaves_tooth_alone(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Чистка состояние зуба не меняет — исход у услуги не задан."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.cleaning_id, "teeth": [21]}])
    await _set_tooth(client, ctx, 21, "caries")

    await client.post(f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers)
    assert (await _odontogram(client, ctx))[21] == "caries"


async def test_cancelled_item_does_not_touch_tooth(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """От работы отказались — карту она не меняет."""
    appointment_id = await _book(
        client,
        ctx,
        [{"serviceId": ctx.extraction_id, "teeth": [22], "status": "cancelled"}],
    )
    await _set_tooth(client, ctx, 22, "caries")

    await client.post(
        f"{API}/clinic/appointments/{appointment_id}/complete",
        headers=ctx.headers,
        json={"noCharge": True, "noChargeComment": "Пациент передумал"},
    )
    assert (await _odontogram(client, ctx))[22] == "caries"


async def test_unfinished_appointment_does_not_sync(
    client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Пока приём идёт, карта не трогается — лечение ещё не факт."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [24]}])
    await _set_tooth(client, ctx, 24, "caries")

    await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=ctx.headers,
        json={"status": "in_progress"},
    )
    assert (await _odontogram(client, ctx))[24] == "caries"


# ── 3. Спорные случаи ───────────────────────────────────────────────────────


async def test_last_work_on_the_tooth_wins(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Запломбировали, потом удалили — в карте удаление, а не пломба.

    Позиции идут в чеке по порядку; побеждает последняя, иначе состояние зуба
    зависело бы от того, какую строку выбрал Postgres.
    """
    appointment_id = await _book(
        client,
        ctx,
        [
            {"serviceId": ctx.filling_id, "teeth": [46]},
            {"serviceId": ctx.extraction_id, "teeth": [46]},
        ],
    )
    await client.post(f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers)
    assert (await _odontogram(client, ctx))[46] == "missing"


async def test_outcome_is_editable_and_removable(client: AsyncClient, ctx: ClinicCtx) -> None:
    """Исход настраивается у услуги и снимается — синк перестаёт срабатывать."""
    patched = await client.patch(
        f"{API}/clinic/services/{ctx.filling_id}",
        headers=ctx.headers,
        json={"toothOutcome": "crown"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["toothOutcome"] == "crown"

    appointment_id = await _book(client, ctx, [{"serviceId": ctx.filling_id, "teeth": [26]}])
    await client.post(f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers)
    assert (await _odontogram(client, ctx))[26] == "crown"

    cleared = await client.patch(
        f"{API}/clinic/services/{ctx.filling_id}", headers=ctx.headers, json={"toothOutcome": None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["toothOutcome"] is None


async def test_other_clinic_sees_updated_card(
    admin_client: AsyncClient, client: AsyncClient, ctx: ClinicCtx
) -> None:
    """Одонтограмма глобальная: результат лечения виден следующей клинике."""
    appointment_id = await _book(client, ctx, [{"serviceId": ctx.extraction_id, "teeth": [16]}])
    await client.post(f"{API}/clinic/appointments/{appointment_id}/complete", headers=ctx.headers)

    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    other = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Б",
            "adminPhone": phone,
            "name": "Клиника Б",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert other.status_code == 201, other.text
    other_headers = await otp_login(client, phone)
    # Клиника видит карту только при наличии приёма — заводим свой.
    service_b = await _service(client, other_headers, "Осмотр", None)
    await client.post(
        f"{API}/clinic/appointments",
        headers=other_headers,
        json={
            "patientId": ctx.patient_id,
            "doctorMembershipId": await own_membership_id(client, other_headers),
            "scheduledAt": working_slot(),
            "items": [{"serviceId": service_b}],
        },
    )

    card = await client.get(f"{API}/clinic/patients/{ctx.patient_id}", headers=other_headers)
    assert card.status_code == 200, card.text
    statuses = {t["tooth"]: t["status"] for t in card.json()["odontogram"]}
    assert statuses[16] == "missing"
