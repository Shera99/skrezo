"""API-тесты заявок клиник (второй путь регистрации).

Подача возможна только после подтверждённого OTP: телефон берётся из билета, а не
из тела. Рассмотрение (список/карточка/статусы) — только для команды Skrezo.

Ключевые риски:

* заявку нельзя подать без подтверждения телефона (и на чужой номер);
* билет одноразовый, но ошибка валидации его не сжигает;
* админ-контур закрыт от сотрудников клиник и без токена;
* статусы меняются и фиксируют, кто их поставил;
* очередь одна на две двери: лид с лендинга (``POST /leads``, телефон не подтверждён)
  виден тем же админам тем же списком — с ``phoneVerified = false``, без компании и
  города; фильтр по двери и счётчики статусов не расходятся.
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.modules.clinics.application.services.provisioning_service import (
    ProvisionCommand,
    build_provisioning_service,
)
from app.shared.db.enums import ClinicType
from app.shared.db.models.clinic_application import ClinicApplication
from app.shared.domain.value_objects import normalize_phone
from tests.conftest import API, Resources, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070099{next(_phone_seq):04d}"


async def _ticket(client: AsyncClient, res: Resources, phone: str) -> str:
    """Пройти шаги 1–2 (запрос кода + подтверждение) и получить билет."""
    requested = await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text
    code: str | None = await res.fake.get(f"auth:otp:code:login:{normalize_phone(phone)}")
    assert code is not None
    verify = await client.post(f"{API}/auth/otp/verify", json={"phone": phone, "code": code})
    assert verify.status_code == 200, verify.text
    token: str = verify.json()["registrationToken"]
    return token


async def _city(client: AsyncClient) -> str:
    city: str = (await client.get(f"{API}/cities")).json()[0]["id"]
    return city


async def _submit(
    client: AsyncClient,
    res: Resources,
    *,
    name: str = "Дента+",
    doctors: str = "6-10",
) -> dict[str, Any]:
    """Подать заявку целиком (OTP → форма). Вернуть тело ответа."""
    phone = _phone()
    token = await _ticket(client, res, phone)
    resp = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": token,
            "companyName": name,
            "cityId": await _city(client),
            "doctorsCount": doctors,
        },
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    body["_phone"] = normalize_phone(phone)
    return body


# ── 1. Подача заявки ────────────────────────────────────────────────────────


async def test_submit_application_after_otp(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Заявка подаётся после OTP и попадает в админку со статусом pending."""
    submitted = await _submit(client, _resources, name="Клиника Мечты", doctors="10+")
    assert submitted["status"] == "pending"

    listed = await admin_client.get(f"{API}/admin/clinic-applications")
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json()["list"] if x["id"] == submitted["id"])
    assert row["companyName"] == "Клиника Мечты"
    assert row["doctorsCount"] == "10+"
    assert row["phone"] == submitted["_phone"]  # телефон из OTP, не из тела
    assert row["cityName"]  # город локализован
    assert row["status"] == "pending"
    assert listed.json()["statusCounts"]["pending"] >= 1


async def test_submit_without_ticket_is_400(client: AsyncClient) -> None:
    """Без подтверждённого телефона заявка не принимается."""
    resp = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": "bogus",
            "companyName": "Ничья",
            "cityId": await _city(client),
            "doctorsCount": "2-5",
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"] == "registration_not_verified"


async def test_ticket_is_single_use(client: AsyncClient, _resources: Resources) -> None:
    """Билет одноразовый: вторая заявка тем же токеном → 400."""
    phone = _phone()
    token = await _ticket(client, _resources, phone)
    payload = {
        "registrationToken": token,
        "companyName": "Дубль",
        "cityId": await _city(client),
        "doctorsCount": "2-5",
    }
    first = await client.post(f"{API}/clinic-applications", json=payload)
    assert first.status_code == 201, first.text
    second = await client.post(f"{API}/clinic-applications", json=payload)
    assert second.status_code == 400, second.text


async def test_unknown_city_is_422_and_ticket_survives(
    client: AsyncClient, _resources: Resources
) -> None:
    """Неизвестный город → 422, билет не сгорает (можно исправить и повторить)."""
    phone = _phone()
    token = await _ticket(client, _resources, phone)
    bad = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": token,
            "companyName": "Город?",
            "cityId": "00000000-0000-4000-8000-000000000000",
            "doctorsCount": "2-5",
        },
    )
    assert bad.status_code == 422, bad.text
    assert bad.json()["error"] == "city_not_found"

    good = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": token,
            "companyName": "Город!",
            "cityId": await _city(client),
            "doctorsCount": "2-5",
        },
    )
    assert good.status_code == 201, good.text


async def test_invalid_doctors_count_is_422(client: AsyncClient, _resources: Resources) -> None:
    """Размер клиники вне списка (2-5 / 6-10 / 10+) → 422."""
    phone = _phone()
    token = await _ticket(client, _resources, phone)
    resp = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": token,
            "companyName": "Много",
            "cityId": await _city(client),
            "doctorsCount": "100500",
        },
    )
    assert resp.status_code == 422, resp.text


# ── 2. Рассмотрение в админке ───────────────────────────────────────────────


async def test_admin_reads_and_changes_status(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Карточка читается; статус меняется и фиксирует, кто его поставил."""
    submitted = await _submit(client, _resources, name="Статусная")
    application_id = submitted["id"]

    card = await admin_client.get(f"{API}/admin/clinic-applications/{application_id}")
    assert card.status_code == 200, card.text
    assert card.json()["reviewedAt"] is None

    reviewed = await admin_client.patch(
        f"{API}/admin/clinic-applications/{application_id}/status",
        json={"status": "reviewed"},
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "reviewed"
    assert reviewed.json()["reviewedAt"] is not None
    assert reviewed.json()["reviewedByName"]  # кто из команды Skrezo

    # Решение можно пересмотреть — переходы свободные.
    rejected = await admin_client.patch(
        f"{API}/admin/clinic-applications/{application_id}/status",
        json={"status": "rejected"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"


async def test_admin_filters_by_status(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Фильтр по статусу отдаёт только заявки в этом статусе."""
    pending = await _submit(client, _resources, name="Ждёт")
    other = await _submit(client, _resources, name="Отклонена")
    await admin_client.patch(
        f"{API}/admin/clinic-applications/{other['id']}/status", json={"status": "rejected"}
    )

    only_rejected = await admin_client.get(f"{API}/admin/clinic-applications?status=rejected")
    ids = {x["id"] for x in only_rejected.json()["list"]}
    assert other["id"] in ids
    assert pending["id"] not in ids


async def test_admin_unknown_application_is_404(admin_client: AsyncClient) -> None:
    """Карточка и смена статуса несуществующей заявки → 404."""
    missing = uuid.uuid4()
    card = await admin_client.get(f"{API}/admin/clinic-applications/{missing}")
    assert card.status_code == 404, card.text
    assert card.json()["error"] == "application_not_found"

    patched = await admin_client.patch(
        f"{API}/admin/clinic-applications/{missing}/status", json={"status": "reviewed"}
    )
    assert patched.status_code == 404, patched.text


async def test_clinic_staff_cannot_read_applications(
    client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Сотрудник клиники не имеет доступа к заявкам (нужен platform_admin)."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": phone,
            "name": "Не для заявок",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(client, phone)

    resp = await client.get(f"{API}/admin/clinic-applications", headers=headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "not_platform_admin"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/admin/clinic-applications"),
        ("GET", "/admin/clinic-applications?phoneVerified=false"),
        ("GET", f"/admin/clinic-applications/{uuid.uuid4()}"),
        ("PATCH", f"/admin/clinic-applications/{uuid.uuid4()}/status"),
    ],
)
async def test_admin_endpoints_without_token_401(
    client: AsyncClient, method: str, path: str
) -> None:
    """Админ-ручки заявок закрыты авторизацией."""
    resp = await client.request(method, f"{API}{path}", json={"status": "reviewed"})
    assert resp.status_code == 401, resp.text


# ── 3. Единая очередь: лиды с лендинга рядом с заявками из приложения ───────

# Полный набор ключей карточки/строки списка (camelCase) — контракт для фронта.
_ITEM_KEYS = {
    "id",
    "companyName",
    "cityId",
    "cityName",
    "doctorsCount",
    "fullName",
    "phone",
    "phoneVerified",
    "source",
    "comment",
    "status",
    "reviewedByAccountId",
    "reviewedByName",
    "reviewedAt",
    "createdAt",
}
_EMPTY_COMPANY_FIELDS = ("companyName", "cityId", "cityName", "doctorsCount")


async def _lead(public: AsyncClient, **extra: Any) -> dict[str, Any]:
    """Оставить лид с лендинга (дверь №2: без OTP). Вернуть ответ + нормализованный номер."""
    body: dict[str, Any] = {"fullName": "Лид Лидов", "phone": _phone(), **extra}
    resp = await public.post(f"{API}/leads", json=body)
    assert resp.status_code == 201, resp.text
    out: dict[str, Any] = resp.json()
    out["_phone"] = normalize_phone(body["phone"])
    return out


async def _list(admin: AsyncClient, query: str = "") -> dict[str, Any]:
    resp = await admin.get(f"{API}/admin/clinic-applications{query}")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _by_id(listed: dict[str, Any], application_id: str) -> dict[str, Any]:
    return next(x for x in listed["list"] if x["id"] == application_id)


async def test_admin_list_shows_landing_lead_as_unverified_without_company_fields(
    public_client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Лид виден в списке: ``phoneVerified=false``, имя, source, comment; компании и города нет."""
    lead = await _lead(public_client, source="instagram", comment="Перезвоните после 14:00")

    row = _by_id(await _list(admin_client), lead["id"])

    assert set(row) == _ITEM_KEYS
    assert row["phoneVerified"] is False
    assert row["fullName"] == "Лид Лидов"
    assert row["phone"] == lead["_phone"]  # нормализован, как у заявки через OTP
    assert row["source"] == "instagram"
    assert row["comment"] == "Перезвоните после 14:00"
    assert row["status"] == "pending"
    for key in _EMPTY_COMPANY_FIELDS:
        assert row[key] is None, key
    assert row["reviewedAt"] is None
    assert row["reviewedByAccountId"] is None
    assert row["reviewedByName"] is None


async def test_admin_list_shows_lead_with_clinic_name_and_range_but_no_city(
    public_client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Если лид прислал название и диапазон — они видны, а города всё равно нет (outer join)."""
    lead = await _lead(public_client, clinicName="Дента+", doctorsCountRange="10+")

    row = _by_id(await _list(admin_client), lead["id"])

    assert row["companyName"] == "Дента+"
    assert row["doctorsCount"] == "10+"
    assert row["cityId"] is None
    assert row["cityName"] is None
    assert row["phoneVerified"] is False


async def test_admin_list_shows_otp_application_as_verified_without_name(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Заявка через OTP: ``phoneVerified=true``, имени/source/comment нет, компания и город есть."""
    submitted = await _submit(client, _resources, name="Клиника Мечты", doctors="2-5")

    row = _by_id(await _list(admin_client), submitted["id"])

    assert set(row) == _ITEM_KEYS
    assert row["phoneVerified"] is True
    assert row["fullName"] is None
    assert row["source"] is None
    assert row["comment"] is None
    assert row["companyName"] == "Клиника Мечты"
    assert row["doctorsCount"] == "2-5"
    assert row["cityId"] and row["cityName"]


async def test_admin_list_mixes_both_doors_newest_first(
    client: AsyncClient,
    public_client: AsyncClient,
    admin_client: AsyncClient,
    _resources: Resources,
) -> None:
    """Обе двери — одна очередь: свежие сверху, ``totalCount`` считает и лиды, и заявки."""
    first = await _submit(client, _resources, name="Первая")
    lead = await _lead(public_client)
    last = await _submit(client, _resources, name="Третья")

    listed = await _list(admin_client)

    assert [x["id"] for x in listed["list"]] == [last["id"], lead["id"], first["id"]]
    assert listed["totalCount"] == 3
    assert [x["phoneVerified"] for x in listed["list"]] == [True, False, True]


async def test_admin_filter_phone_verified_returns_only_the_chosen_door(
    client: AsyncClient,
    public_client: AsyncClient,
    admin_client: AsyncClient,
    _resources: Resources,
) -> None:
    """``phoneVerified``: true — только заявки через OTP, false — только лиды, без него — все."""
    otp_ids = {(await _submit(client, _resources))["id"] for _ in range(2)}
    lead_ids = {(await _lead(public_client))["id"] for _ in range(2)}

    verified = await _list(admin_client, "?phoneVerified=true")
    unverified = await _list(admin_client, "?phoneVerified=false")
    everything = await _list(admin_client)

    assert {x["id"] for x in verified["list"]} == otp_ids
    assert verified["totalCount"] == 2
    assert all(x["phoneVerified"] is True for x in verified["list"])
    assert {x["id"] for x in unverified["list"]} == lead_ids
    assert unverified["totalCount"] == 2
    assert all(x["phoneVerified"] is False for x in unverified["list"])
    assert {x["id"] for x in everything["list"]} == otp_ids | lead_ids
    assert everything["totalCount"] == 4


async def test_admin_filter_phone_verified_combines_with_status_and_paging(
    public_client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Фильтр двери складывается с фильтром статуса и пагинацией (totalCount — по обоим)."""
    kept = await _lead(public_client)
    resolved = await _lead(public_client)
    third = await _lead(public_client)
    await admin_client.patch(
        f"{API}/admin/clinic-applications/{resolved['id']}/status", json={"status": "reviewed"}
    )

    pending = await _list(admin_client, "?phoneVerified=false&status=pending&pageSize=1&page=2")

    assert pending["totalCount"] == 2  # kept и third; resolved отфильтрован по статусу
    assert pending["currentPage"] == 2
    assert len(pending["list"]) == 1
    assert pending["list"][0]["id"] == kept["id"]  # вторая страница = более старый из двух
    assert third["id"] not in {x["id"] for x in pending["list"]}


async def test_admin_filter_phone_verified_invalid_value_is_422(admin_client: AsyncClient) -> None:
    """Не булево значение фильтра → 422, а не «показать всё»."""
    resp = await admin_client.get(f"{API}/admin/clinic-applications?phoneVerified=maybe")
    assert resp.status_code == 422, resp.text


async def test_status_counts_follow_phone_verified_filter_but_not_status_filter(
    client: AsyncClient,
    public_client: AsyncClient,
    admin_client: AsyncClient,
    _resources: Resources,
) -> None:
    """Чипы статусов считаются в пределах выбранной двери, но не сужаются фильтром статуса.

    Очередь: лиды — 2 pending + 1 reviewed; заявки через OTP — 1 pending + 2 rejected.
    Чтобы чипы совпадали со списком, при ``phoneVerified`` счётчики берутся только
    по этой двери; при ``status`` — остаются полными (по ним переключают статус).
    """
    await _lead(public_client)
    await _lead(public_client)
    reviewed_lead = await _lead(public_client)
    await _submit(client, _resources)
    rejected = [await _submit(client, _resources) for _ in range(2)]
    await admin_client.patch(
        f"{API}/admin/clinic-applications/{reviewed_lead['id']}/status",
        json={"status": "reviewed"},
    )
    for application in rejected:
        await admin_client.patch(
            f"{API}/admin/clinic-applications/{application['id']}/status",
            json={"status": "rejected"},
        )

    def nonzero(listed: dict[str, Any]) -> dict[str, int]:
        return {k: v for k, v in listed["statusCounts"].items() if v}

    everything = await _list(admin_client)
    leads_only = await _list(admin_client, "?phoneVerified=false")
    otp_only = await _list(admin_client, "?phoneVerified=true")
    leads_pending = await _list(admin_client, "?phoneVerified=false&status=pending")

    assert nonzero(everything) == {"pending": 3, "reviewed": 1, "rejected": 2}
    assert nonzero(leads_only) == {"pending": 2, "reviewed": 1}
    assert nonzero(otp_only) == {"pending": 1, "rejected": 2}
    # Сумма чипов == размер списка без фильтра статуса — иначе бейджи врут.
    for listed in (everything, leads_only, otp_only):
        assert sum(listed["statusCounts"].values()) == listed["totalCount"]
    # Фильтр статуса список сужает, а чипы — нет.
    assert leads_pending["totalCount"] == 2
    assert nonzero(leads_pending) == {"pending": 2, "reviewed": 1}


async def test_admin_card_of_lead_returns_comment_source_and_name(
    public_client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Карточка ``GET /{id}``: то, что написал человек (comment) и метка трафика не теряются."""
    lead = await _lead(
        public_client,
        fullName="Айгүл Асанова",
        comment="Позвоните вечером",
        source="google",
        clinicName="Дента+",
        doctorsCountRange="6-10",
    )

    card = await admin_client.get(f"{API}/admin/clinic-applications/{lead['id']}")

    assert card.status_code == 200, card.text
    body = card.json()
    assert set(body) == _ITEM_KEYS
    assert body["fullName"] == "Айгүл Асанова"
    assert body["comment"] == "Позвоните вечером"
    assert body["source"] == "google"
    assert body["companyName"] == "Дента+"
    assert body["doctorsCount"] == "6-10"
    assert body["phoneVerified"] is False
    assert body["cityId"] is None
    assert body["cityName"] is None
    assert body["phone"] == lead["_phone"]


async def test_admin_card_of_otp_application_is_marked_verified(
    client: AsyncClient, admin_client: AsyncClient, _resources: Resources
) -> None:
    """Карточка заявки через OTP: ``phoneVerified=true``, без имени и без метки трафика."""
    submitted = await _submit(client, _resources)

    body = (await admin_client.get(f"{API}/admin/clinic-applications/{submitted['id']}")).json()

    assert body["phoneVerified"] is True
    assert body["fullName"] is None
    assert body["source"] is None
    assert body["comment"] is None


async def test_admin_changes_lead_status_keeps_fields_and_writes_audit(
    public_client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Смена статуса лида: ответ — полная карточка, кто и когда зафиксированы, аудит пишется."""
    lead = await _lead(public_client, source="instagram", comment="Здравствуйте")

    reviewed = await admin_client.patch(
        f"{API}/admin/clinic-applications/{lead['id']}/status", json={"status": "reviewed"}
    )

    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert body["status"] == "reviewed"
    assert body["reviewedAt"] is not None
    assert body["reviewedByName"] == "Test Admin"
    assert body["reviewedByAccountId"]
    # Ответ — всё та же карточка лида, ничего не обнулилось при обновлении.
    assert body["phoneVerified"] is False
    assert body["fullName"] == "Лид Лидов"
    assert body["source"] == "instagram"
    assert body["comment"] == "Здравствуйте"
    for key in _EMPTY_COMPANY_FIELDS:
        assert body[key] is None, key

    journal = await admin_client.get(f"{API}/admin/audit", params={"limit": 100})
    assert journal.status_code == 200, journal.text
    entries = [
        e
        for e in journal.json()["items"]
        if e["action"] == "clinic_application.status_changed" and e["entityId"] == lead["id"]
    ]
    assert len(entries) == 1
    assert entries[0]["entityType"] == "clinic_application"
    assert entries[0]["payload"] == {"status": "reviewed"}
    assert entries[0]["actorId"] == body["reviewedByAccountId"]


async def test_lead_status_can_be_revised_and_reopened_lead_blocks_repeat_again(
    public_client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Переходы свободные и у лида: rejected → pending возвращает его в «открытые» для дедупа."""
    lead = await _lead(public_client)
    phone = lead["_phone"]
    path = f"{API}/admin/clinic-applications/{lead['id']}/status"

    assert (await admin_client.patch(path, json={"status": "rejected"})).status_code == 200
    reopened = await admin_client.patch(path, json={"status": "pending"})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "pending"

    repeat = await public_client.post(f"{API}/leads", json={"fullName": "Снова", "phone": phone})
    assert repeat.status_code == 409, repeat.text
    assert repeat.json()["error"] == "already_submitted"


async def test_clinic_owner_cannot_touch_landing_leads(
    client: AsyncClient, public_client: AsyncClient, admin_client: AsyncClient
) -> None:
    """Владелец клиники не видит и не меняет лиды: в них персональные данные посторонних людей."""
    lead = await _lead(public_client)
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    owner_phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": owner_phone,
            "name": "Не для лидов",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    headers = await otp_login(public_client, owner_phone)

    responses = [
        await public_client.get(
            f"{API}/admin/clinic-applications?phoneVerified=false", headers=headers
        ),
        await public_client.get(f"{API}/admin/clinic-applications/{lead['id']}", headers=headers),
        await public_client.patch(
            f"{API}/admin/clinic-applications/{lead['id']}/status",
            headers=headers,
            json={"status": "reviewed"},
        ),
    ]

    for resp in responses:
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"] == "not_platform_admin"
        assert lead["_phone"] not in resp.text
    unchanged = await admin_client.get(f"{API}/admin/clinic-applications/{lead['id']}")
    assert unchanged.json()["status"] == "pending"


# ── 4. Конверсия: заявка → заведённая клиника ───────────────────────────────


async def test_provisioning_with_application_id_marks_application_as_converted(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """``ProvisionCommand.application_id`` проставляет ``converted_clinic_id`` у заявки очереди.

    Раньше это делал ``lead_id`` на отдельной таблице лидов; теперь конвертируется
    строка ``clinic_application`` — и лид, и заявка через OTP.
    """
    lead = await _lead(public_client)
    async with _resources.factory() as session:
        result = await build_provisioning_service(session).provision(
            ProvisionCommand(
                type=ClinicType.SOLO,
                admin_full_name="Конвертированный",
                admin_phone=lead["_phone"],
                application_id=uuid.UUID(lead["id"]),
            )
        )

    async with _resources.factory() as session:
        row = (
            await session.execute(
                select(ClinicApplication).where(ClinicApplication.id == uuid.UUID(lead["id"]))
            )
        ).scalar_one()
    assert row.converted_clinic_id == result.clinic.id


async def test_mark_converted_for_unknown_application_changes_nothing(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Несуществующий id заявки не ломает конвертацию и не трогает чужие строки."""
    other = await _lead(public_client)
    async with _resources.factory() as session:
        result = await build_provisioning_service(session).provision(
            ProvisionCommand(
                type=ClinicType.SOLO,
                admin_full_name="Без заявки",
                admin_phone="0700990999",
                application_id=uuid.uuid4(),
            )
        )
        assert result.clinic.id

    async with _resources.factory() as session:
        row = (
            await session.execute(
                select(ClinicApplication).where(ClinicApplication.id == uuid.UUID(other["id"]))
            )
        ).scalar_one()
    assert row.converted_clinic_id is None
