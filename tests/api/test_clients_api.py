"""API-тесты раздела «Клиенты» (список, создание, врачи, проверки)."""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import API, otp_login, working_slot


async def _first_city_id(admin_client: AsyncClient) -> str:
    resp = await admin_client.get(f"{API}/admin/refs/cities")
    assert resp.status_code == 200
    return str(resp.json()[0]["id"])


async def _first_spec_id(admin_client: AsyncClient) -> str:
    resp = await admin_client.get(f"{API}/admin/refs/specializations")
    assert resp.status_code == 200
    return str(resp.json()[0]["id"])


async def test_create_solo_and_list(admin_client: AsyncClient) -> None:
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Соло Врач", "adminPhone": "0700600001"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["type"] == "solo"
    assert body["adminPhone"] == "+996700600001"
    # Пароль не выдаётся: владелец входит по коду на свой номер.
    assert "tempPassword" not in body

    listing = await admin_client.get(f"{API}/admin/clients?page=1")
    assert listing.status_code == 200
    page = listing.json()
    assert page["totalCount"] == 1
    item = page["list"][0]
    assert item["tariffName"] == "Соло"
    assert item["subscriptionStatus"] == "trial"
    assert item["trialDay"] == 1


async def test_phone_check_and_dedup(admin_client: AsyncClient) -> None:
    await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Занятый", "adminPhone": "0700600002"},
    )
    taken = await admin_client.get(f"{API}/admin/clients/phone-check?phone=0700600002")
    assert taken.json()["ownerName"] == "Занятый"
    free = await admin_client.get(f"{API}/admin/clients/phone-check?phone=0700699999")
    assert free.json()["ownerName"] is None

    dup = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Дубль", "adminPhone": "0700600002"},
    )
    assert dup.status_code == 409
    assert dup.json()["error"] == "phone_already_used"


async def test_status_counts(admin_client: AsyncClient) -> None:
    await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Счёт", "adminPhone": "0700600003"},
    )
    resp = await admin_client.get(f"{API}/admin/clients/status-counts")
    assert resp.status_code == 200
    assert resp.json()["trial"] == 1
    assert resp.json()["cancelled"] == 0


async def test_patch_client(admin_client: AsyncClient) -> None:
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Правка", "adminPhone": "0700600004"},
    )
    cid = created.json()["id"]
    patched = await admin_client.patch(
        f"{API}/admin/clients/{cid}", json={"notes": "важный", "language": "en"}
    )
    assert patched.status_code == 200
    assert patched.json()["notes"] == "важный"
    assert patched.json()["language"] == "en"


async def test_admin_doctor_lifecycle(admin_client: AsyncClient) -> None:
    city = await _first_city_id(admin_client)
    spec = await _first_spec_id(admin_client)
    clinic = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Клиника Владелец",
            "adminPhone": "0700600005",
            "name": "Тест-Клиника",
            "cityId": city,
            "businessPhone": "0700600000",
            "seats": 5,
        },
    )
    assert clinic.status_code == 201, clinic.text
    cid = clinic.json()["id"]

    add = await admin_client.post(
        f"{API}/admin/clients/{cid}/doctors",
        json={"fullName": "Доктор Тест", "phone": "0700600006", "specializationId": spec},
    )
    assert add.status_code == 201, add.text
    doc_id = add.json()["id"]
    assert "tempPassword" not in add.json()

    upd = await admin_client.patch(
        f"{API}/admin/clients/{cid}/doctors/{doc_id}", json={"fullName": "Доктор Новый"}
    )
    assert upd.status_code == 200
    assert upd.json()["role"] == "doctor"
    assert upd.json()["fullName"] == "Доктор Новый"
    assert upd.json()["active"] is True

    card = await admin_client.get(f"{API}/admin/clients/{cid}")
    doctor = next(s for s in card.json()["staff"] if s["id"] == doc_id)
    assert doctor["active"] is True
    assert card.json()["seatsUsed"] == 1

    rem = await admin_client.delete(f"{API}/admin/clients/{cid}/doctors/{doc_id}")
    assert rem.status_code == 200
    assert rem.json()["mode"] == "deactivated"

    # Врач остаётся в карточке (на нём висит история), но помечен неактивным и
    # места в тарифе не занимает — иначе уволенный неотличим от работающего.
    after = await admin_client.get(f"{API}/admin/clients/{cid}")
    removed = next(s for s in after.json()["staff"] if s["id"] == doc_id)
    assert removed["active"] is False
    assert after.json()["seatsUsed"] == 0


async def test_doctor_on_solo_is_400(admin_client: AsyncClient) -> None:
    spec = await _first_spec_id(admin_client)
    clinic = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Соло", "adminPhone": "0700600007"},
    )
    cid = clinic.json()["id"]
    resp = await admin_client.post(
        f"{API}/admin/clients/{cid}/doctors",
        json={"fullName": "X Y", "phone": "0700600008", "specializationId": spec},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "solo_no_staff"


async def test_client_card_shows_real_patients_and_revenue(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Метрики клиента считаются из ядра и кассы, а не стоят нулями.

    Раньше Skrezo по карточке не мог понять, живёт клиника или числится:
    `patientsCount` и `revenueTiyin` были заглушками со времён «клиническое
    ядро вне скоупа».
    """
    city = await _first_city_id(admin_client)
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Метрик",
            "adminPhone": "0700600900",
            "name": "Клиника Метрик",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    clinic_id = created.json()["id"]

    headers = await otp_login(client, "0700600900")

    patient = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": "0700600901", "firstName": "Пациент", "lastName": "Метриков"},
    )
    service = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Лечение", "priceTiyin": 5000}
    )
    appointment = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": patient.json()["id"],
            "scheduledAt": working_slot(),
            "items": [{"serviceId": service.json()["id"], "teeth": [46]}],
        },
    )
    assert appointment.status_code == 201, appointment.text
    paid = await client.post(
        f"{API}/clinic/cash/payments",
        headers=headers,
        json={
            "appointmentId": appointment.json()["id"],
            "amountTiyin": 5000,
            "method": "cash",
        },
    )
    assert paid.status_code == 201, paid.text

    card = await admin_client.get(f"{API}/admin/clients/{clinic_id}")
    assert card.status_code == 200, card.text
    assert card.json()["patientsCount"] == 1
    assert card.json()["revenueTiyin"] == 5000
    assert card.json()["activity"]["patients"] == 1
    assert card.json()["activity"]["visits"] == 1
    # Сколько клиент заплатил Skrezo — это другое поле, и оно ждёт биллинга.
    assert card.json()["lifetimeRevenueTiyin"] is None

    listed = await admin_client.get(f"{API}/admin/clients")
    row = next(r for r in listed.json()["list"] if r["id"] == clinic_id)
    assert row["patientsCount"] == 1
    assert row["revenueTiyin"] == 5000


async def test_client_without_activity_reports_zeros(admin_client: AsyncClient) -> None:
    """Клиника без приёмов — нули, а не пропуск строки в списке."""
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Пустой", "adminPhone": "0700600902"},
    )
    listed = await admin_client.get(f"{API}/admin/clients")
    row = next(r for r in listed.json()["list"] if r["id"] == created.json()["id"])
    assert row["patientsCount"] == 0
    assert row["revenueTiyin"] == 0
