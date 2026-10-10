"""API-тесты публичной лид-формы лендинга (``POST /leads``) — вторая дверь очереди заявок.

Лид ложится в ту же ``clinic_application``, что и заявка через OTP, но с пометкой
«телефон не подтверждён». Читать очередь команде — в ``test_clinic_applications_api``
(админка) и ``test_notifications`` (чат команды); здесь — приём и дедуп.

Ключевые риски:

* форма лендинга шлёт ровно два поля (имя и телефон) — любая «обязательность»
  лишнего поля молча роняет все заявки с сайта;
* лид не должен подменять данные: ``phone_verified`` всегда ``false``, диапазон
  врачей придумывать нельзя, пробельные строки — это ``NULL``, а не пустые значения
  в очереди;
* дедуп идёт строго по нормализованному телефону и в строгом порядке
  (аккаунт → открытая заявка любой двери → создать), а разобранные заявки
  повтор не блокируют;
* гонка двух одновременных отправок не плодит дубль (частичный уникальный индекс);
* контракт OpenAPI — на него опирается фронт (nullable-поля, два примера 409).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.main import app
from app.modules.clinic_applications.domain.rules import parse_doctors_count_range
from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
    ClinicApplicationRepository,
)
from app.shared.db.enums import ClinicApplicationStatus, DoctorsCountRange
from app.shared.db.models.clinic_application import ClinicApplication
from tests.conftest import API, Resources, otp_code

_phone_seq = itertools.count(1)


def _phone() -> str:
    """Новый локальный номер КГ (в БД ляжет как ``+996700770NNN``)."""
    return f"0700770{next(_phone_seq):03d}"


def _e164(local: str) -> str:
    return "+996" + local.lstrip("0")


async def _rows(res: Resources, phone: str | None = None) -> list[ClinicApplication]:
    """Все заявки (или по номеру) из БД — старые сверху."""
    async with res.factory() as session:
        query = select(ClinicApplication).order_by(ClinicApplication.created_at)
        if phone is not None:
            query = query.where(ClinicApplication.phone == phone)
        return list((await session.execute(query)).scalars().all())


async def _count(res: Resources) -> int:
    async with res.factory() as session:
        return int(
            (
                await session.execute(select(func.count()).select_from(ClinicApplication))
            ).scalar_one()
        )


async def _otp_application(
    client: AsyncClient, phone: str, *, name: str = "Дента+"
) -> dict[str, Any]:
    """Подать заявку через OTP (дверь №1): запрос кода → подтверждение → форма."""
    requested = await client.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text
    verified = await client.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await otp_code(phone)}
    )
    assert verified.status_code == 200, verified.text
    city = (await client.get(f"{API}/cities")).json()[0]["id"]
    resp = await client.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": verified.json()["registrationToken"],
            "companyName": name,
            "cityId": city,
            "doctorsCount": "6-10",
        },
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def _create_clinic_owner(
    admin: AsyncClient, phone: str, *, owner: str = "Секретный Владелец", clinic: str = "Тайная"
) -> None:
    """Завести клинику с владельцем на этот номер — у номера появляется аккаунт."""
    city = (await admin.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    created = await admin.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": owner,
            "adminPhone": phone,
            "name": clinic,
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text


# ── 1. Как лендинг: имя и телефон, больше ничего ────────────────────────────


async def test_lead_with_name_and_phone_only_is_created_without_token(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Ровно то, что шлёт лендинг: ``{fullName, phone}`` без токена и без clinicName → 201.

    В очереди — неподтверждённая заявка ``pending``; всё, чего форма не спрашивает,
    остаётся пустым (а не пустой строкой и не именем человека в названии клиники).
    """
    resp = await public_client.post(
        f"{API}/leads", json={"fullName": "Тест Тестов", "phone": "+996555000001"}
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"id", "createdAt"}  # прежний контракт ответа

    (row,) = await _rows(_resources)
    assert str(row.id) == body["id"]  # id ответа — id строки очереди
    assert row.created_at == datetime.fromisoformat(body["createdAt"])
    assert row.full_name == "Тест Тестов"
    assert row.phone == "+996555000001"
    assert row.phone_verified is False
    assert row.status is ClinicApplicationStatus.PENDING
    assert row.company_name is None
    assert row.city_id is None
    assert row.doctors_count is None
    assert row.source is None
    assert row.comment is None
    assert row.converted_clinic_id is None
    assert row.reviewed_by_account_id is None
    assert row.reviewed_at is None


@pytest.mark.parametrize(
    "raw",
    ["0700123456", "0700 123 456", "+996700123456", "996700123456", "+996 (700) 12-34-56"],
)
async def test_lead_phone_is_stored_normalized(
    public_client: AsyncClient, _resources: Resources, raw: str
) -> None:
    """Телефон в любом формате ложится в БД одним каноническим видом (E.164)."""
    resp = await public_client.post(f"{API}/leads", json={"fullName": "Формат", "phone": raw})
    assert resp.status_code == 201, resp.text

    (row,) = await _rows(_resources)
    assert row.phone == "+996700123456"


# ── 2. Необязательные поля и валидация ──────────────────────────────────────


async def test_lead_with_all_fields_saves_each_of_them(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Полное тело: название клиники, диапазон, комментарий и source доходят до очереди."""
    resp = await public_client.post(
        f"{API}/leads",
        json={
            "fullName": "Айгүл Асанова",
            "phone": "0700 123 456",
            "clinicName": "Дента+",
            "doctorsCountRange": "6-10",
            "comment": "Перезвоните после 14:00",
            "source": "instagram",
        },
    )
    assert resp.status_code == 201, resp.text

    (row,) = await _rows(_resources)
    assert row.company_name == "Дента+"
    assert row.doctors_count is DoctorsCountRange.FROM_6_TO_10
    assert row.comment == "Перезвоните после 14:00"
    assert row.source == "instagram"
    assert row.city_id is None  # город на лендинге не спрашиваем
    assert row.phone_verified is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2-5", DoctorsCountRange.FROM_2_TO_5),
        ("6-10", DoctorsCountRange.FROM_6_TO_10),
        ("10+", DoctorsCountRange.OVER_10),
        ("  6-10 ", DoctorsCountRange.FROM_6_TO_10),  # пробелы по краям — не помеха
        ("2-3", None),  # свой диапазон — не угадываем
        ("много", None),
        ("10", None),
        ("11+", None),
        ("6 - 10", None),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
async def test_lead_doctors_count_range_maps_to_enum_only_for_exact_values(
    public_client: AsyncClient,
    _resources: Resources,
    raw: str | None,
    expected: DoctorsCountRange | None,
) -> None:
    """Свободный текст попадает в ``doctors_count`` только при точном совпадении.

    Придумывать диапазон за человека нельзя: менеджер уточнит на звонке. Неузнанное
    значение — это ``NULL``, а не 422 (форма не должна терять лид из-за поля).
    """
    resp = await public_client.post(
        f"{API}/leads",
        json={"fullName": "Диапазон", "phone": _phone(), "doctorsCountRange": raw},
    )
    assert resp.status_code == 201, resp.text

    (row,) = await _rows(_resources)
    assert row.doctors_count is expected


async def test_lead_blank_optional_strings_become_null(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Пробельные clinicName/comment/source — это «не прислали», а не пустая строка в очереди."""
    resp = await public_client.post(
        f"{API}/leads",
        json={
            "fullName": "Пустышки",
            "phone": _phone(),
            "clinicName": "   ",
            "comment": " \n\t ",
            "source": "  ",
            "doctorsCountRange": "  ",
        },
    )
    assert resp.status_code == 201, resp.text

    (row,) = await _rows(_resources)
    assert row.company_name is None
    assert row.comment is None
    assert row.source is None
    assert row.doctors_count is None


async def test_lead_text_fields_are_trimmed(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Пробелы по краям текстовых полей срезаются (в очереди нет « Дента+ » и «instagram »)."""
    resp = await public_client.post(
        f"{API}/leads",
        json={
            "fullName": "  Айгүл  ",
            "phone": _phone(),
            "clinicName": " Дента+ ",
            "comment": "  позвоните  ",
            "source": " instagram ",
        },
    )
    assert resp.status_code == 201, resp.text

    (row,) = await _rows(_resources)
    assert row.full_name == "Айгүл"
    assert row.company_name == "Дента+"
    assert row.comment == "позвоните"
    assert row.source == "instagram"


async def test_lead_source_boundary_200_ok_201_is_422(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """source: ровно 200 символов принимается, 201 → 422 и заявка не создаётся."""
    ok = await public_client.post(
        f"{API}/leads", json={"fullName": "Граница", "phone": _phone(), "source": "s" * 200}
    )
    assert ok.status_code == 201, ok.text
    (row,) = await _rows(_resources)
    assert row.source == "s" * 200

    too_long = await public_client.post(
        f"{API}/leads", json={"fullName": "Граница", "phone": _phone(), "source": "s" * 201}
    )
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["error"] == "validation_error"
    assert too_long.json()["details"][0]["loc"][-1] == "source"
    assert await _count(_resources) == 1


async def test_lead_comment_boundary_2000_ok_2001_is_422(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """comment: 2000 символов принимается, 2001 → 422."""
    ok = await public_client.post(
        f"{API}/leads", json={"fullName": "Граница", "phone": _phone(), "comment": "к" * 2000}
    )
    assert ok.status_code == 201, ok.text

    too_long = await public_client.post(
        f"{API}/leads", json={"fullName": "Граница", "phone": _phone(), "comment": "к" * 2001}
    )
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["details"][0]["loc"][-1] == "comment"
    assert await _count(_resources) == 1


@pytest.mark.parametrize(
    ("field", "limit"),
    [("fullName", 200), ("clinicName", 200), ("doctorsCountRange", 50)],
)
async def test_lead_text_field_length_boundary_ok_then_422(
    public_client: AsyncClient, _resources: Resources, field: str, limit: int
) -> None:
    """Публичная ручка без токена: ровно ``limit`` символов принимается, на 1 больше → 422.

    Без лимита мусор в десятки мегабайт попадал бы в админский список, задачу arq
    и Telegram (сообщение длиннее 4096 символов Telegram отклоняет).
    """
    body: dict[str, Any] = {"fullName": "Граница", "phone": _phone()}
    ok = await public_client.post(f"{API}/leads", json={**body, field: "я" * limit})
    assert ok.status_code == 201, ok.text
    assert await _count(_resources) == 1

    too_long = await public_client.post(
        f"{API}/leads", json={**body, "phone": _phone(), field: "я" * (limit + 1)}
    )
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["error"] == "validation_error"
    assert too_long.json()["details"][0]["loc"][-1] == field
    assert await _count(_resources) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"phone": "0700770901"},  # нет имени
        {"fullName": None, "phone": "0700770902"},
        {"fullName": "А", "phone": "0700770903"},  # короче двух символов
        {"fullName": "   ", "phone": "0700770904"},  # из одних пробелов — это «нет имени»
        {"fullName": " А ", "phone": "0700770905"},  # после обрезки пробелов — один символ
        {"fullName": "Без телефона"},
        {"fullName": "Клиника-однобуквенная", "phone": "0700770906", "clinicName": "К"},
    ],
    ids=[
        "no_name",
        "null_name",
        "short_name",
        "blank_name",
        "short_after_trim",
        "no_phone",
        "short_clinic_name",
    ],
)
async def test_lead_invalid_body_is_422_and_nothing_saved(
    public_client: AsyncClient, _resources: Resources, body: dict[str, Any]
) -> None:
    """Имя обязательно (min 2 после trim), телефон обязателен, clinicName — min 2, если есть.

    Заявка без имени — строка из одного номера: в очереди её не отличить от мусора
    (ровно это держит и CHECK ``ck_clinic_application_lead_named``).
    """
    resp = await public_client.post(f"{API}/leads", json=body)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "validation_error"
    assert await _count(_resources) == 0


@pytest.mark.parametrize("phone", ["123", "abc", "", "+999 000", "07001"])
async def test_lead_invalid_phone_is_422_invalid_phone(
    public_client: AsyncClient, _resources: Resources, phone: str
) -> None:
    """Кривой телефон → 422 с кодом ``invalid_phone`` (не 500 и не молчаливая запись)."""
    resp = await public_client.post(f"{API}/leads", json={"fullName": "Кривой", "phone": phone})

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "invalid_phone"
    assert await _count(_resources) == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2-5", DoctorsCountRange.FROM_2_TO_5),
        ("6-10", DoctorsCountRange.FROM_6_TO_10),
        ("10+", DoctorsCountRange.OVER_10),
        ("  6-10 ", DoctorsCountRange.FROM_6_TO_10),
        ("\t10+\n", DoctorsCountRange.OVER_10),
        ("2–5", None),  # типографское тире — не дефис: не угадываем
        ("2-3", None),
        ("6-10 врачей", None),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_parse_doctors_count_range_rule(
    raw: str | None, expected: DoctorsCountRange | None
) -> None:
    """Правило разбора само по себе (не полагаясь на обрезку пробелов в схеме запроса)."""
    assert parse_doctors_count_range(raw) is expected


# ── 3. Дедуп: аккаунт → открытая заявка любой двери → создать ───────────────


@pytest.mark.parametrize(
    "second_format",
    ["0700123456", "+996700123456", "996700123456", "0700 123 456", "+996 (700) 12-34-56"],
)
async def test_repeat_lead_same_phone_any_format_is_409_already_submitted(
    public_client: AsyncClient, _resources: Resources, second_format: str
) -> None:
    """Повтор лида по тому же номеру — в любой записи — это 409 ``already_submitted``.

    В теле — ``createdAt`` первой заявки (форма говорит «мы уже получили заявку»);
    новой строки в очереди не появляется.
    """
    first = await public_client.post(
        f"{API}/leads", json={"fullName": "Первый", "phone": "+996 700 12 34 56"}
    )
    assert first.status_code == 201, first.text

    second = await public_client.post(
        f"{API}/leads", json={"fullName": "Второй", "phone": second_format}
    )

    assert second.status_code == 409, second.text
    body = second.json()
    assert body["error"] == "already_submitted"
    assert body["message"]
    assert set(body) == {"error", "message", "createdAt"}
    assert datetime.fromisoformat(body["createdAt"]) == datetime.fromisoformat(
        first.json()["createdAt"]
    )
    rows = await _rows(_resources)
    assert len(rows) == 1
    assert rows[0].full_name == "Первый"  # повтор ничего не перезаписал


async def test_open_otp_application_blocks_lead_with_already_submitted(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Открытая заявка через OTP (другая дверь) блокирует лид по тому же номеру."""
    phone = _phone()
    submitted = await _otp_application(public_client, phone)

    resp = await public_client.post(f"{API}/leads", json={"fullName": "Тот же", "phone": phone})

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "already_submitted"
    (existing,) = await _rows(_resources, _e164(phone))
    assert str(existing.id) == submitted["id"]
    assert datetime.fromisoformat(resp.json()["createdAt"]) == existing.created_at
    assert await _count(_resources) == 1  # лид не создан


async def test_already_submitted_reports_created_at_of_the_freshest_open_application(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """``createdAt`` — время САМОЙ СВЕЖЕЙ открытой заявки; разобранные в счёт не идут."""
    phone = _phone()
    city = (await public_client.get(f"{API}/cities")).json()[0]["id"]

    def row(created: datetime, status: ClinicApplicationStatus) -> ClinicApplication:
        return ClinicApplication(
            phone=_e164(phone),
            phone_verified=True,
            company_name="Дента+",
            city_id=uuid.UUID(city),
            doctors_count=DoctorsCountRange.FROM_6_TO_10,
            status=status,
            created_at=created,
        )

    older = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    newer = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    resolved_later = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    async with _resources.factory() as session:
        session.add_all(
            [
                row(older, ClinicApplicationStatus.PENDING),
                row(newer, ClinicApplicationStatus.PENDING),
                row(resolved_later, ClinicApplicationStatus.REVIEWED),
            ]
        )
        await session.commit()

    resp = await public_client.post(f"{API}/leads", json={"fullName": "Новый", "phone": phone})

    assert resp.status_code == 409, resp.text
    assert datetime.fromisoformat(resp.json()["createdAt"]) == newer


async def test_lead_for_existing_account_phone_is_409_already_client(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Номер, у которого уже есть аккаунт, → 409 ``already_client`` (форма предложит войти).

    Лишнего не раскрываем: ни имени владельца, ни названия клиники, ни времени.
    """
    phone = _phone()
    await _create_clinic_owner(admin_client, phone, owner="Секретный Владелец", clinic="Тайная")

    resp = await public_client.post(f"{API}/leads", json={"fullName": "Случайный", "phone": phone})

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"] == "already_client"
    assert set(body) == {"error", "message"}  # без createdAt и без деталей
    assert "Секретный" not in resp.text
    assert "Тайная" not in resp.text
    assert await _count(_resources) == 0  # заявка не создана


async def test_already_client_matches_account_phone_in_any_format(
    admin_client: AsyncClient, public_client: AsyncClient
) -> None:
    """Аккаунт находится по нормализованному номеру, а не по строке «как ввели»."""
    await _create_clinic_owner(admin_client, "0700770777")

    resp = await public_client.post(
        f"{API}/leads", json={"fullName": "Формат", "phone": "+996 (700) 77-07-77"}
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "already_client"


async def test_solo_doctor_account_phone_is_also_already_client(
    admin_client: AsyncClient, public_client: AsyncClient
) -> None:
    """Соло-врач — тоже аккаунт: по его номеру лид не нужен."""
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={"type": "solo", "adminFullName": "Соло Врач", "adminPhone": phone},
    )
    assert created.status_code == 201, created.text

    resp = await public_client.post(f"{API}/leads", json={"fullName": "Соло", "phone": phone})

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "already_client"


async def test_already_client_takes_priority_over_open_application(
    admin_client: AsyncClient, public_client: AsyncClient
) -> None:
    """Порядок проверок: аккаунт раньше открытой заявки.

    Лид принят → команда завела клиента на этот номер → повторная отправка формы
    должна предложить войти (``already_client``), а не сказать «заявка принята».
    """
    phone = _phone()
    first = await public_client.post(f"{API}/leads", json={"fullName": "Лид", "phone": phone})
    assert first.status_code == 201, first.text
    await _create_clinic_owner(admin_client, phone)

    again = await public_client.post(f"{API}/leads", json={"fullName": "Лид", "phone": phone})

    assert again.status_code == 409, again.text
    assert again.json()["error"] == "already_client"


@pytest.mark.parametrize("door", ["lead", "otp"])
@pytest.mark.parametrize("resolved", ["reviewed", "rejected"])
async def test_resolved_application_does_not_block_new_lead(
    admin_client: AsyncClient,
    public_client: AsyncClient,
    _resources: Resources,
    door: str,
    resolved: str,
) -> None:
    """После ``reviewed``/``rejected`` человек может прийти снова — новый лид принимается.

    Проверено для обеих дверей прежней заявки. Новая открытая заявка снова блокирует
    повтор (дедуп работает по ОТКРЫТЫМ, а не «был ли кто-то вообще»).
    """
    phone = _phone()
    if door == "lead":
        first = await public_client.post(f"{API}/leads", json={"fullName": "Лид", "phone": phone})
        assert first.status_code == 201, first.text
        previous_id = str(first.json()["id"])
    else:
        previous_id = str((await _otp_application(public_client, phone))["id"])

    patched = await admin_client.patch(
        f"{API}/admin/clinic-applications/{previous_id}/status", json={"status": resolved}
    )
    assert patched.status_code == 200, patched.text

    again = await public_client.post(f"{API}/leads", json={"fullName": "Снова", "phone": phone})

    assert again.status_code == 201, again.text
    assert again.json()["id"] != previous_id
    rows = await _rows(_resources, _e164(phone))
    assert sorted(r.status.value for r in rows) == sorted(["pending", resolved])

    third = await public_client.post(f"{API}/leads", json={"fullName": "Ещё раз", "phone": phone})
    assert third.status_code == 409, third.text
    assert third.json()["error"] == "already_submitted"
    assert datetime.fromisoformat(third.json()["createdAt"]) == datetime.fromisoformat(
        again.json()["createdAt"]
    )  # теперь «открытая» — новая, а не старая разобранная


async def _audit_entries(admin: AsyncClient, application_id: str) -> list[dict[str, Any]]:
    """Записи аудита о смене статуса заявки (по ``entityId``)."""
    journal = await admin.get(f"{API}/admin/audit", params={"limit": 100})
    assert journal.status_code == 200, journal.text
    entries: list[dict[str, Any]] = [
        e
        for e in journal.json()["items"]
        if e["action"] == "clinic_application.status_changed" and e["entityId"] == application_id
    ]
    return entries


async def test_reopening_resolved_lead_while_new_one_is_pending_is_409_open_lead_exists(
    admin_client: AsyncClient,
    public_client: AsyncClient,
    _resources: Resources,
) -> None:
    """Вернуть разобранный лид в ``pending`` при уже открытом новом — 409, а не 500.

    Статусы меняются свободно, но частичный уникальный индекс допускает ОДИН
    открытый лид на номер. Попытка открыть второй нарушает индекс: админ получает
    ``open_lead_exists`` с id лида, который надо разобрать первым. Состояние не
    портится (первый лид остался ``reviewed``, аудит о неудавшейся смене не пишется),
    а после разбора открытого лида возврат проходит.
    """
    phone = _phone()
    first = await public_client.post(f"{API}/leads", json={"fullName": "Первый", "phone": phone})
    first_id = first.json()["id"]
    path = f"{API}/admin/clinic-applications/{first_id}/status"
    assert (await admin_client.patch(path, json={"status": "reviewed"})).status_code == 200
    second = await public_client.post(f"{API}/leads", json={"fullName": "Второй", "phone": phone})
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    before = {str(r.id): r for r in await _rows(_resources, _e164(phone))}

    reopened = await admin_client.patch(path, json={"status": "pending"})

    assert reopened.status_code == 409, f"{reopened.status_code}: {reopened.text}"
    body = reopened.json()
    assert body["error"] == "open_lead_exists"
    assert body["openApplicationId"] == second_id
    assert "message" in body
    # Состояние не испорчено: первый лид остался разобранным, второй — открытым.
    after = {str(r.id): r for r in await _rows(_resources, _e164(phone))}
    assert after[first_id].status is ClinicApplicationStatus.REVIEWED
    assert after[first_id].reviewed_at == before[first_id].reviewed_at
    assert after[second_id].status is ClinicApplicationStatus.PENDING
    # Аудит: только успешный переход в reviewed, неудавшийся возврат не записан.
    entries = await _audit_entries(admin_client, first_id)
    assert [e["payload"] for e in entries] == [{"status": "reviewed"}]

    # Совет из ответа работает: разобрали открытый лид — возврат проходит.
    second_path = f"{API}/admin/clinic-applications/{second_id}/status"
    assert (await admin_client.patch(second_path, json={"status": "rejected"})).status_code == 200
    retry = await admin_client.patch(path, json={"status": "pending"})
    assert retry.status_code == 200, retry.text
    assert retry.json()["status"] == "pending"


async def test_reopening_otp_application_is_not_blocked_by_open_lead(
    admin_client: AsyncClient,
    public_client: AsyncClient,
) -> None:
    """Индекс «один открытый лид на номер» не касается заявок через OTP.

    Разобранную заявку из приложения можно вернуть в ``pending``, даже если по
    тому же номеру уже открыт лид с лендинга.
    """
    phone = _phone()
    otp_id = str((await _otp_application(public_client, phone))["id"])
    path = f"{API}/admin/clinic-applications/{otp_id}/status"
    assert (await admin_client.patch(path, json={"status": "reviewed"})).status_code == 200
    lead = await public_client.post(f"{API}/leads", json={"fullName": "Лид", "phone": phone})
    assert lead.status_code == 201, lead.text

    reopened = await admin_client.patch(path, json={"status": "pending"})

    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "pending"


# ── 4. Гонка двух одновременных отправок ────────────────────────────────────


async def test_concurrent_leads_same_phone_create_exactly_one_row(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Четыре одновременные отправки одного номера → один 201, три 409, одна строка."""
    phone = _phone()

    responses = await asyncio.gather(
        *(
            public_client.post(f"{API}/leads", json={"fullName": f"Гонщик {i}", "phone": phone})
            for i in range(4)
        )
    )

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [201, 409, 409, 409], [r.text for r in responses]
    created = next(r for r in responses if r.status_code == 201)
    for loser in (r for r in responses if r.status_code == 409):
        assert loser.json()["error"] == "already_submitted"
        assert datetime.fromisoformat(loser.json()["createdAt"]) == datetime.fromisoformat(
            created.json()["createdAt"]
        )
    rows = await _rows(_resources, _e164(phone))
    assert len(rows) == 1
    assert str(rows[0].id) == created.json()["id"]


async def test_race_loser_is_answered_already_submitted_via_unique_index(
    public_client: AsyncClient, _resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проигравший гонку получает тот же ``already_submitted`` — через частичный индекс.

    Предпроверку «есть ли открытая заявка» глушим на первый вызов (так выглядит
    параллельный запрос, который проверил ДО чужого коммита): вставка упирается в
    ``ux_clinic_application_pending_lead_phone`` настоящей БД, транзакция
    откатывается, повторный поиск находит победителя. Без этой ветки проигравший
    получил бы 500.
    """
    phone = _phone()
    first = await public_client.post(
        f"{API}/leads", json={"fullName": "Победитель", "phone": phone}
    )
    assert first.status_code == 201, first.text

    original = ClinicApplicationRepository.last_pending_created_at
    calls: list[str] = []

    async def blind_once(self: ClinicApplicationRepository, number: str) -> datetime | None:
        calls.append(number)
        if len(calls) == 1:
            return None  # «на момент проверки открытой заявки ещё не было»
        return await original(self, number)

    monkeypatch.setattr(ClinicApplicationRepository, "last_pending_created_at", blind_once)

    loser = await public_client.post(
        f"{API}/leads", json={"fullName": "Проигравший", "phone": phone}
    )

    assert loser.status_code == 409, loser.text
    assert loser.json()["error"] == "already_submitted"
    assert datetime.fromisoformat(loser.json()["createdAt"]) == datetime.fromisoformat(
        first.json()["createdAt"]
    )
    assert len(calls) == 2, "ветка IntegrityError не отработала: повторного поиска не было"
    assert len(await _rows(_resources, _e164(phone))) == 1


async def test_other_integrity_errors_are_not_masked_as_already_submitted(
    public_client: AsyncClient, _resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Чужая ошибка целостности — 500, а не вежливое «заявка уже принята».

    Самый коварный случай: по номеру ЕСТЬ открытая заявка (повторный поиск её
    найдёт), а вставка упала НЕ на индексе дедупа, а на другом ограничении (здесь
    настоящий CHECK «у лида обязательно имя»). Если проверять только «открытая заявка
    нашлась», настоящий баг спрятался бы за 409 и заявки терялись молча.
    """
    phone = _phone()
    first = await public_client.post(f"{API}/leads", json={"fullName": "Первый", "phone": phone})
    assert first.status_code == 201, first.text

    original_create = ClinicApplicationRepository.create
    original_search = ClinicApplicationRepository.last_pending_created_at
    searches: list[str] = []

    async def nameless(self: ClinicApplicationRepository, **kwargs: Any) -> ClinicApplication:
        return await original_create(self, **{**kwargs, "full_name": None})

    async def blind_once(self: ClinicApplicationRepository, number: str) -> datetime | None:
        searches.append(number)
        return None if len(searches) == 1 else await original_search(self, number)

    monkeypatch.setattr(ClinicApplicationRepository, "create", nameless)
    monkeypatch.setattr(ClinicApplicationRepository, "last_pending_created_at", blind_once)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        resp = await anon.post(f"{API}/leads", json={"fullName": "Имя есть", "phone": phone})

    assert resp.status_code == 500, resp.text
    assert resp.json()["error"] == "internal_error"
    assert await _count(_resources) == 1  # вторая заявка не создана


# ── 5. Контракт OpenAPI ─────────────────────────────────────────────────────


@pytest.fixture
def schema() -> dict[str, Any]:
    spec: dict[str, Any] = app.openapi()
    return spec


def test_openapi_lead_request_only_name_and_phone_are_required(schema: dict[str, Any]) -> None:
    """В теле POST /leads обязательны только fullName и phone; есть source с лимитом 200."""
    request = schema["components"]["schemas"]["CreateLeadRequest"]

    assert set(request["required"]) == {"fullName", "phone"}
    assert "clinicName" in request["properties"]
    # clinicName — nullable (форма лендинга его не шлёт).
    assert {"type": "null"} in request["properties"]["clinicName"]["anyOf"]
    source = request["properties"]["source"]
    assert {"type": "null"} in source["anyOf"]
    assert {"type": "string", "maxLength": 200} in source["anyOf"]
    assert {"type": "string", "maxLength": 2000} in request["properties"]["comment"]["anyOf"]


def test_openapi_lead_request_text_fields_have_length_limits(schema: dict[str, Any]) -> None:
    """Публичная форма: у fullName/clinicName/doctorsCountRange в контракте есть maxLength."""
    props = schema["components"]["schemas"]["CreateLeadRequest"]["properties"]

    assert props["fullName"]["maxLength"] == 200
    assert {"type": "string", "maxLength": 200, "minLength": 2} in props["clinicName"]["anyOf"]
    assert {"type": "string", "maxLength": 50} in props["doctorsCountRange"]["anyOf"]


def test_openapi_status_change_documents_open_lead_conflict(schema: dict[str, Any]) -> None:
    """PATCH /admin/clinic-applications/{id}/status: 409 ``open_lead_exists`` описан с примером."""
    op = schema["paths"]["/api/v1/admin/clinic-applications/{application_id}/status"]["patch"]

    example = op["responses"]["409"]["content"]["application/json"]["example"]
    assert example["error"] == "open_lead_exists"
    assert "openApplicationId" in example


def test_openapi_lead_endpoint_is_public_and_documented(schema: dict[str, Any]) -> None:
    """POST /leads: тег ``leads``, без security, описаны 201/409/422/429."""
    op = schema["paths"]["/api/v1/leads"]["post"]

    assert op["tags"] == ["leads"]
    assert op["summary"]
    assert not op.get("security"), "публичная лид-форма не должна требовать токен"
    assert {"201", "409", "422", "429"} <= set(op["responses"])
    assert op["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/LeadCreatedResponse"
    }


def test_openapi_lead_409_has_two_named_examples(schema: dict[str, Any]) -> None:
    """409 описан двумя именованными примерами: ``already_submitted`` и ``already_client``."""
    examples = schema["paths"]["/api/v1/leads"]["post"]["responses"]["409"]["content"][
        "application/json"
    ]["examples"]

    assert set(examples) == {"already_submitted", "already_client"}
    assert examples["already_submitted"]["value"]["error"] == "already_submitted"
    assert "createdAt" in examples["already_submitted"]["value"]
    assert examples["already_client"]["value"]["error"] == "already_client"
    assert "createdAt" not in examples["already_client"]["value"]


def test_openapi_old_lead_conflict_code_is_gone(schema: dict[str, Any]) -> None:
    """Код ``lead_already_exists`` больше не используется ни в схеме, ни в коде приложения."""
    assert "lead_already_exists" not in json.dumps(schema, ensure_ascii=False)


def test_openapi_application_item_fields_are_nullable_and_new_fields_present(
    schema: dict[str, Any],
) -> None:
    """ApplicationItem: companyName/cityId/cityName/doctorsCount — nullable; есть новые поля."""
    item = schema["components"]["schemas"]["ApplicationItem"]
    props = item["properties"]

    for name in ("companyName", "cityId", "cityName", "doctorsCount"):
        assert {"type": "null"} in props[name]["anyOf"], f"{name} должен быть nullable"
    for name in ("fullName", "source", "comment"):
        assert {"type": "null"} in props[name]["anyOf"], f"{name} должен быть nullable"

    # Признак двери — обязательный булев (не nullable): фронту нельзя «угадывать».
    assert props["phoneVerified"]["type"] == "boolean"
    assert {"phone", "phoneVerified", "status", "id", "createdAt"} <= set(item["required"])
    for name in ("fullName", "phoneVerified", "source", "comment", "phone"):
        assert props[name].get("description"), f"у {name} нет описания"
    assert len(item["examples"]) == 2  # лид и OTP-заявка


def test_openapi_list_has_phone_verified_filter(schema: dict[str, Any]) -> None:
    """GET /admin/clinic-applications принимает необязательный булев phoneVerified."""
    op = schema["paths"]["/api/v1/admin/clinic-applications"]["get"]
    param = next(p for p in op["parameters"] if p["name"] == "phoneVerified")

    assert param["in"] == "query"
    assert param.get("required", False) is False
    assert {"type": "boolean"} in param["schema"]["anyOf"]
    assert "statusCounts" in op["description"]  # описано, что счётчики учитывают фильтр
