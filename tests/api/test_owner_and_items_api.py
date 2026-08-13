"""API-тесты: владелец принимает пациентов и услуги в составе приёма.

Две дырки, найденные при разборе прошлой итерации:

* «врач» = активное членство СО специализацией, а владельцу клиники её не
  ставили — он не колонка в ленте дня, и на него нельзя записать. В клинике из
  двух человек это значит, что принимающий владелец не может вести приём;
* проверка «врач выполняет услугу» стояла на `serviceId` приёма, который задаёт
  лишь длительность. Деньги и карта зуба считаются из позиций — туда можно было
  положить что угодно.
"""

from __future__ import annotations

import itertools
from datetime import date, timedelta
from typing import Any

from httpx import AsyncClient

from tests.conftest import API, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070087{next(_phone_seq):04d}"


def _slot(hour: int = 10) -> str:
    day = date.today() + timedelta(days=1)
    if day.weekday() == 6:  # воскресенье — выходной по умолчанию
        day += timedelta(days=1)
    return f"{day.isoformat()}T{hour:02d}:00:00+06:00"


async def _spec_ids(admin_client: AsyncClient) -> list[str]:
    resp = await admin_client.get(f"{API}/admin/refs/specializations")
    return [s["id"] for s in resp.json()]


async def _clinic(
    admin_client: AsyncClient, client: AsyncClient, *, specialization_id: str | None = None
) -> tuple[str, dict[str, str]]:
    """Клиника с владельцем; вернуть (clinicId, заголовки владельца)."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    body: dict[str, Any] = {
        "type": "clinic",
        "adminFullName": "Владелец Принимающий",
        "adminPhone": phone,
        "name": f"Клиника {phone}",
        "cityId": city,
        "businessPhone": "0700000000",
        "seats": 5,
    }
    if specialization_id is not None:
        body["specializationId"] = specialization_id
    created = await admin_client.post(f"{API}/admin/clients", json=body)
    assert created.status_code == 201, created.text
    return str(created.json()["id"]), await otp_login(client, phone)


async def _own_membership(client: AsyncClient, headers: dict[str, str]) -> str:
    me = await client.get(f"{API}/auth/me", headers=headers)
    return str(me.json()["activeClinic"]["membershipId"])


async def _patient(client: AsyncClient, headers: dict[str, str]) -> str:
    resp = await client.post(
        f"{API}/clinic/patients",
        headers=headers,
        json={"phone": _phone(), "firstName": "Пациент", "lastName": "Владельцев"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


# ── 1. Владелец принимает ───────────────────────────────────────────────────


async def test_owner_with_specialization_can_take_patients(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Задали специализацию при заведении — владелец сразу врач."""
    spec = (await _spec_ids(admin_client))[0]
    _, headers = await _clinic(admin_client, client, specialization_id=spec)
    owner_id = await _own_membership(client, headers)

    # Колонка в ленте дня появилась.
    day = await client.get(
        f"{API}/clinic/schedule/day?date={_slot()[:10]}", headers=headers
    )
    assert owner_id in [c["membershipId"] for c in day.json()["doctors"]]

    # И на него записывают.
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": await _patient(client, headers),
            "doctorMembershipId": owner_id,
            "scheduledAt": _slot(),
        },
    )
    assert booked.status_code == 201, booked.text


async def test_owner_without_specialization_is_not_a_doctor(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Владелец-администратор врачом не становится — это осознанный выбор клиники."""
    _, headers = await _clinic(admin_client, client)
    owner_id = await _own_membership(client, headers)

    day = await client.get(
        f"{API}/clinic/schedule/day?date={_slot()[:10]}", headers=headers
    )
    assert day.json()["doctors"] == []

    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": await _patient(client, headers),
            "doctorMembershipId": owner_id,
            "scheduledAt": _slot(),
        },
    )
    assert booked.status_code == 422, booked.text
    assert booked.json()["error"] == "invalid_doctor"


async def test_owner_sets_own_specialization_from_cabinet(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Владелец включает себе приём сам, не обращаясь в поддержку Skrezo."""
    _, headers = await _clinic(admin_client, client)
    owner_id = await _own_membership(client, headers)
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]

    updated = await client.put(
        f"{API}/clinic/staff/{owner_id}", headers=headers, json={"specializationId": spec}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["specializationId"] == spec

    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": await _patient(client, headers),
            "doctorMembershipId": owner_id,
            "scheduledAt": _slot(11),
        },
    )
    assert booked.status_code == 201, booked.text


async def test_owner_other_fields_stay_closed(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Через staff-флоу владельцу по-прежнему не сменить ФИО, телефон и роль."""
    _, headers = await _clinic(admin_client, client)
    owner_id = await _own_membership(client, headers)
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]

    for payload in (
        {"fullName": "Новое Имя"},
        {"phone": _phone()},
        {"specializationId": spec, "fullName": "И то и другое"},
    ):
        resp = await client.put(
            f"{API}/clinic/staff/{owner_id}", headers=headers, json=payload
        )
        assert resp.status_code == 403, f"{payload}: {resp.text}"
        assert resp.json()["error"] == "cannot_edit_owner"


async def test_unknown_specialization_is_422_not_500(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Неизвестная специализация в карточке клиента — понятная ошибка, а не падение.

    Раньше id уходил в базу и падал нарушением внешнего ключа.
    """
    clinic_id, _ = await _clinic(admin_client, client)
    resp = await admin_client.patch(
        f"{API}/admin/clients/{clinic_id}",
        json={"specializationId": "00000000-0000-4000-8000-000000000000"},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "specialization_not_found"


# ── 2. Услуги в позициях приёма ─────────────────────────────────────────────


async def _doctor_with_service(
    admin_client: AsyncClient, client: AsyncClient, headers: dict[str, str], name: str
) -> str:
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    added = await client.post(
        f"{API}/clinic/staff",
        headers=headers,
        json={"fullName": name, "phone": _phone(), "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    return str(added.json()["membershipId"])


async def test_item_service_is_checked_on_create(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Услугу, которую врач не делает, нельзя положить в состав приёма."""
    spec = (await _spec_ids(admin_client))[0]
    _, headers = await _clinic(admin_client, client, specialization_id=spec)
    owner_id = await _own_membership(client, headers)
    other_id = await _doctor_with_service(admin_client, client, headers, "Врач Второй")

    implant = await client.post(
        f"{API}/clinic/services",
        headers=headers,
        json={"name": "Имплантация", "priceTiyin": 90000, "doctorIds": [other_id]},
    )
    assert implant.status_code == 201, implant.text
    service_id = implant.json()["id"]

    # Основная услуга не задана — раньше это и было дырой.
    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": await _patient(client, headers),
            "doctorMembershipId": owner_id,
            "scheduledAt": _slot(),
            "items": [{"serviceId": service_id, "teeth": [46]}],
        },
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "doctor_cant_do_service"
    assert resp.json()["serviceIds"] == [service_id]


async def test_item_service_is_checked_on_replace(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """И при замене чека — работы становятся известны уже по ходу приёма."""
    spec = (await _spec_ids(admin_client))[0]
    _, headers = await _clinic(admin_client, client, specialization_id=spec)
    owner_id = await _own_membership(client, headers)
    other_id = await _doctor_with_service(admin_client, client, headers, "Врач Второй")

    implant = await client.post(
        f"{API}/clinic/services",
        headers=headers,
        json={"name": "Имплантация", "priceTiyin": 90000, "doctorIds": [other_id]},
    )
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": await _patient(client, headers),
            "doctorMembershipId": owner_id,
            "scheduledAt": _slot(),
        },
    )
    assert booked.status_code == 201, booked.text

    resp = await client.put(
        f"{API}/clinic/appointments/{booked.json()['id']}/items",
        headers=headers,
        json={"items": [{"serviceId": implant.json()["id"], "teeth": [46]}]},
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "doctor_cant_do_service"


async def test_force_lets_owner_put_any_service_in_the_bill(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Обход тот же, что и на записи: администратор клиники решает сам."""
    spec = (await _spec_ids(admin_client))[0]
    _, headers = await _clinic(admin_client, client, specialization_id=spec)
    owner_id = await _own_membership(client, headers)
    other_id = await _doctor_with_service(admin_client, client, headers, "Врач Второй")

    implant = await client.post(
        f"{API}/clinic/services",
        headers=headers,
        json={"name": "Имплантация", "priceTiyin": 90000, "doctorIds": [other_id]},
    )
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": await _patient(client, headers),
            "doctorMembershipId": owner_id,
            "scheduledAt": _slot(),
        },
    )
    resp = await client.put(
        f"{API}/clinic/appointments/{booked.json()['id']}/items",
        headers=headers,
        json={"items": [{"serviceId": implant.json()["id"], "teeth": [46]}], "force": True},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["priceTotalTiyin"] == 90000


async def test_all_offending_services_are_listed_at_once(
    admin_client: AsyncClient, client: AsyncClient
) -> None:
    """Ошибка перечисляет все проблемные услуги — чинить по одной незачем."""
    spec = (await _spec_ids(admin_client))[0]
    _, headers = await _clinic(admin_client, client, specialization_id=spec)
    owner_id = await _own_membership(client, headers)
    other_id = await _doctor_with_service(admin_client, client, headers, "Врач Второй")

    first = await client.post(
        f"{API}/clinic/services",
        headers=headers,
        json={"name": "Имплантация", "priceTiyin": 90000, "doctorIds": [other_id]},
    )
    second = await client.post(
        f"{API}/clinic/services",
        headers=headers,
        json={"name": "Хирургия", "priceTiyin": 50000, "doctorIds": [other_id]},
    )
    allowed = await client.post(
        f"{API}/clinic/services", headers=headers, json={"name": "Осмотр", "priceTiyin": 1000}
    )

    resp = await client.post(
        f"{API}/clinic/appointments",
        headers=headers,
        json={
            "patientId": await _patient(client, headers),
            "doctorMembershipId": owner_id,
            "scheduledAt": _slot(),
            "items": [
                {"serviceId": allowed.json()["id"], "teeth": []},
                {"serviceId": first.json()["id"], "teeth": [46]},
                {"serviceId": second.json()["id"], "teeth": [47]},
            ],
        },
    )
    assert resp.status_code == 422, resp.text
    assert set(resp.json()["serviceIds"]) == {first.json()["id"], second.json()["id"]}
