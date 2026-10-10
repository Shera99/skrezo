"""API-тесты конверсии заявки в клиента: ``POST /admin/clients`` с ``applicationId``.

Команда Skrezo заводит клиента из очереди заявок (лид с лендинга или заявка через
OTP). Дашборд считает конверсию «заявки → клиенты» по ``converted_clinic_id``,
поэтому ручка обязана проставлять его надёжно и ровно один раз.

Ключевые риски:

* заявку нельзя конвертировать дважды — в том числе двумя одновременными запросами
  (блокировка ``SELECT ... FOR UPDATE``): проигравший получает 409 и **ничего** не
  создаёт, а не вторую клинику с дублем аккаунта;
* любая ошибка заведения (занятый телефон, нет города, сбой посреди транзакции)
  оставляет заявку нетронутой — конверсия и создание клиента атомарны;
* неизвестный ``applicationId`` не превращается в «тихое» заведение клиента без
  конверсии — это 422, и клиент не создаётся;
* ``reviewed`` ставится только если заявка ещё не рассмотрена; чужой рассмотревший
  и его время не затираются, а аудит смены статуса не дублируется;
* ручка остаётся закрытой для не-админов (401/403), а без ``applicationId`` ведёт
  себя как раньше и заявок не трогает;
* контракт OpenAPI (необязательный ``applicationId``, новые коды ошибок,
  обязательное nullable ``convertedClinicId``) — на него опирается фронт.
"""

from __future__ import annotations

import asyncio
import itertools
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select, update

from app.core.security import hash_password
from app.main import app
from app.modules.clinic_applications.infrastructure.repositories.clinic_application_repository import (  # noqa: E501
    ClinicApplicationRepository,
)
from app.modules.clinic_applications.presentation.schemas import ApplicationItem
from app.shared.db.enums import ClinicApplicationStatus, Lang
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_application import ClinicApplication
from app.shared.db.models.membership import Membership
from app.shared.db.models.role import Role
from app.shared.db.models.subscription import Subscription
from app.shared.domain.value_objects import normalize_phone
from tests.conftest import API, Resources, otp_code, otp_login

_ADMIN_PHONE = "0700000001"  # платформенный админ из фикстуры admin_token
_seq = itertools.count(1)


def _phone() -> str:
    """Новый локальный номер КГ; в одном тесте номера не повторяются."""
    return f"070088{next(_seq):04d}"


# ── Помощники ───────────────────────────────────────────────────────────────


async def _lead(public: AsyncClient, **extra: Any) -> dict[str, str]:
    """Оставить лид с лендинга (``POST /leads``). Вернуть ``{id, phone}``; phone — как отправили."""
    phone = _phone()
    resp = await public.post(
        f"{API}/leads", json={"fullName": "Лид Лидов", "phone": phone, **extra}
    )
    assert resp.status_code == 201, resp.text
    return {"id": str(resp.json()["id"]), "phone": phone}


async def _otp_application(public: AsyncClient) -> dict[str, str]:
    """Подать заявку через OTP (дверь №1). Вернуть ``{id, phone}``."""
    phone = _phone()
    requested = await public.post(f"{API}/auth/otp/request", json={"phone": phone})
    assert requested.status_code == 200, requested.text
    verified = await public.post(
        f"{API}/auth/otp/verify", json={"phone": phone, "code": await otp_code(phone)}
    )
    assert verified.status_code == 200, verified.text
    city = (await public.get(f"{API}/cities")).json()[0]["id"]
    resp = await public.post(
        f"{API}/clinic-applications",
        json={
            "registrationToken": verified.json()["registrationToken"],
            "companyName": "Дента+",
            "cityId": city,
            "doctorsCount": "6-10",
        },
    )
    assert resp.status_code == 201, resp.text
    return {"id": str(resp.json()["id"]), "phone": phone}


async def _convert(
    admin: AsyncClient, application_id: str | None, *, phone: str | None = None, **extra: Any
) -> Response:
    """``POST /admin/clients`` (по умолчанию соло) с ``applicationId`` или без него."""
    body: dict[str, Any] = {
        "type": "solo",
        "adminFullName": "Новый Владелец",
        "adminPhone": phone or _phone(),
        **extra,
    }
    if application_id is not None:
        body["applicationId"] = application_id
    return await admin.post(f"{API}/admin/clients", json=body)


async def _row(res: Resources, application_id: str) -> ClinicApplication:
    async with res.factory() as session:
        return (
            await session.execute(
                select(ClinicApplication).where(ClinicApplication.id == uuid.UUID(application_id))
            )
        ).scalar_one()


async def _admin_id(res: Resources) -> uuid.UUID:
    async with res.factory() as session:
        return (
            await session.execute(
                select(Account.id).where(Account.phone == normalize_phone(_ADMIN_PHONE))
            )
        ).scalar_one()


@dataclass(frozen=True, slots=True)
class _Counts:
    """Сколько строк «клиентского» состояния в БД — чтобы доказать «ничего не создано»."""

    clinics: int
    accounts: int
    memberships: int
    subscriptions: int
    clinic_roles: int


async def _counts(res: Resources) -> _Counts:
    async with res.factory() as session:

        async def count(model: Any, *where: Any) -> int:
            query = select(func.count()).select_from(model).where(*where)
            return int((await session.execute(query)).scalar_one())

        return _Counts(
            clinics=await count(Clinic),
            accounts=await count(Account),
            memberships=await count(Membership),
            subscriptions=await count(Subscription),
            clinic_roles=await count(Role, Role.clinic_id.is_not(None)),
        )


async def _account_exists(res: Resources, phone: str) -> bool:
    async with res.factory() as session:
        found = (
            await session.execute(select(Account.id).where(Account.phone == normalize_phone(phone)))
        ).first()
    return found is not None


async def _audit(admin: AsyncClient, *, action: str, entity_id: str | None = None) -> list[Any]:
    """Записи платформенного журнала по действию (и, если задан, по ``entityId``)."""
    journal = await admin.get(f"{API}/admin/audit", params={"limit": 100})
    assert journal.status_code == 200, journal.text
    items: list[Any] = journal.json()["items"]
    return [
        e
        for e in items
        if e["action"] == action and (entity_id is None or e["entityId"] == entity_id)
    ]


def _assert_untouched(row: ClinicApplication, status: ClinicApplicationStatus) -> None:
    """Заявка не помечена: нет клиники, прежний статус, никто её не «рассматривал»."""
    assert row.converted_clinic_id is None
    assert row.status is status
    assert row.reviewed_by_account_id is None
    assert row.reviewed_at is None


async def _second_admin(res: Resources, public: AsyncClient) -> tuple[uuid.UUID, dict[str, str]]:
    """Второй сотрудник Skrezo (со своим входом) — «другой админ» для проверок рассмотревшего."""
    async with res.factory() as session:
        account = Account(
            phone=normalize_phone("0700000002"),
            full_name="Второй Админ",
            lang=Lang.RU,
            onboarded=True,
            password_hash=hash_password("admin456"),
            must_change_password=False,
            is_platform_admin=True,
        )
        session.add(account)
        await session.commit()
        account_id = account.id
    login = await public.post(
        f"{API}/auth/login", json={"phone": "0700000002", "password": "admin456"}
    )
    assert login.status_code == 200, login.text
    return account_id, {"Authorization": f"Bearer {login.json()['accessToken']}"}


# ── 1. Без applicationId всё как раньше ─────────────────────────────────────


@pytest.mark.parametrize("extra", [{}, {"applicationId": None}], ids=["нет_поля", "null"])
async def test_create_client_without_application_id_works_as_before_and_touches_no_application(
    admin_client: AsyncClient,
    public_client: AsyncClient,
    _resources: Resources,
    extra: dict[str, Any],
) -> None:
    """Без ``applicationId`` (или с ``null``) клиент заводится, а очередь заявок не меняется."""
    bystander = await _lead(public_client)

    resp = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "solo",
            "adminFullName": "Обычный Соло",
            "adminPhone": "0700880001",
            **extra,
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"id", "type", "adminPhone"}
    assert body["type"] == "solo"
    assert body["adminPhone"] == "+996700880001"
    _assert_untouched(await _row(_resources, bystander["id"]), ClinicApplicationStatus.PENDING)

    created = await _audit(admin_client, action="clinic.created", entity_id=body["id"])
    assert len(created) == 1
    assert created[0]["payload"] == {"type": "solo"}  # ключа applicationId нет вовсе
    assert await _audit(admin_client, action="clinic_application.status_changed") == []


# ── 2. Лид → клиент ─────────────────────────────────────────────────────────


async def test_convert_lead_creates_client_and_marks_application_reviewed(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Лид → ``POST /admin/clients`` с ``applicationId``: клиент создан, заявка сконвертирована.

    Проверяется БД, карточка, список (включая счётчики статусов) и аудит.
    """
    lead = await _lead(public_client, source="instagram", comment="Перезвоните после 14:00")
    admin_id = await _admin_id(_resources)
    before = await _counts(_resources)
    started = datetime.now(UTC)

    resp = await _convert(admin_client, lead["id"], phone=lead["phone"])

    assert resp.status_code == 201, resp.text
    clinic_id = resp.json()["id"]
    after = await _counts(_resources)
    assert after.clinics == before.clinics + 1
    assert after.accounts == before.accounts + 1
    assert after.memberships == before.memberships + 1
    assert after.subscriptions == before.subscriptions + 1

    # БД: заявка указывает на новую клинику, рассмотрел вызвавший админ, время — «сейчас».
    row = await _row(_resources, lead["id"])
    assert row.converted_clinic_id == uuid.UUID(clinic_id)
    assert row.status is ClinicApplicationStatus.REVIEWED
    assert row.reviewed_by_account_id == admin_id
    assert row.reviewed_at is not None
    assert (
        started - timedelta(seconds=5)
        <= row.reviewed_at
        <= datetime.now(UTC) + timedelta(seconds=5)
    )

    # Карточка отдаёт конверсию, а остальное содержимое лида не потерялось.
    card = await admin_client.get(f"{API}/admin/clinic-applications/{lead['id']}")
    assert card.status_code == 200, card.text
    body = card.json()
    assert body["convertedClinicId"] == clinic_id
    assert body["status"] == "reviewed"
    assert body["reviewedByAccountId"] == str(admin_id)
    assert body["reviewedByName"] == "Test Admin"
    assert body["reviewedAt"] is not None
    assert body["phoneVerified"] is False
    assert body["fullName"] == "Лид Лидов"
    assert body["source"] == "instagram"
    assert body["comment"] == "Перезвоните после 14:00"

    # Список: то же значение, а счётчики статусов переехали из pending в reviewed.
    listed = (await admin_client.get(f"{API}/admin/clinic-applications")).json()
    item = next(x for x in listed["list"] if x["id"] == lead["id"])
    assert item["convertedClinicId"] == clinic_id
    assert item["status"] == "reviewed"
    assert {k: v for k, v in listed["statusCounts"].items() if v} == {"reviewed": 1}
    reviewed_only = (
        await admin_client.get(f"{API}/admin/clinic-applications?status=reviewed")
    ).json()
    assert [x["id"] for x in reviewed_only["list"]] == [lead["id"]]

    # Аудит: создание клиента с applicationId и отдельная запись о смене статуса.
    created = await _audit(admin_client, action="clinic.created", entity_id=clinic_id)
    assert len(created) == 1
    assert created[0]["entityType"] == "clinic"
    assert created[0]["actorId"] == str(admin_id)
    assert created[0]["payload"] == {"type": "solo", "applicationId": lead["id"]}
    changed = await _audit(
        admin_client, action="clinic_application.status_changed", entity_id=lead["id"]
    )
    assert len(changed) == 1
    assert changed[0]["entityType"] == "clinic_application"
    assert changed[0]["actorId"] == str(admin_id)
    assert changed[0]["payload"] == {
        "status": "reviewed",
        "reason": "converted",
        "clinicId": clinic_id,
    }


async def test_pending_application_has_converted_clinic_id_null_in_card_and_list(
    admin_client: AsyncClient, public_client: AsyncClient
) -> None:
    """Пока клиента не завели, ``convertedClinicId`` есть в ответе и равен ``null``."""
    lead = await _lead(public_client)

    card = (await admin_client.get(f"{API}/admin/clinic-applications/{lead['id']}")).json()
    listed = (await admin_client.get(f"{API}/admin/clinic-applications")).json()
    item = next(x for x in listed["list"] if x["id"] == lead["id"])

    assert "convertedClinicId" in card and card["convertedClinicId"] is None
    assert "convertedClinicId" in item and item["convertedClinicId"] is None


async def test_conversion_status_audit_has_the_same_shape_as_patch_status_audit(
    admin_client: AsyncClient, public_client: AsyncClient
) -> None:
    """Запись о смене статуса при конверсии — та же, что у ``PATCH .../status``, плюс причина.

    Те же action/entityType/entityId/actor и поле ``status``; единственное отличие —
    два дополнительных ключа payload (``reason`` и ``clinicId``).
    """
    patched = await _lead(public_client)
    converted = await _lead(public_client)
    assert (
        await admin_client.patch(
            f"{API}/admin/clinic-applications/{patched['id']}/status", json={"status": "reviewed"}
        )
    ).status_code == 200
    resp = await _convert(admin_client, converted["id"])
    assert resp.status_code == 201, resp.text

    [by_patch] = await _audit(
        admin_client, action="clinic_application.status_changed", entity_id=patched["id"]
    )
    [by_conversion] = await _audit(
        admin_client, action="clinic_application.status_changed", entity_id=converted["id"]
    )

    assert set(by_conversion) == set(by_patch)  # те же поля записи журнала
    assert by_conversion["entityType"] == by_patch["entityType"] == "clinic_application"
    assert by_conversion["actorId"] == by_patch["actorId"]
    assert by_conversion["payload"]["status"] == by_patch["payload"]["status"] == "reviewed"
    assert set(by_conversion["payload"]) == set(by_patch["payload"]) | {"reason", "clinicId"}


async def test_convert_clinic_type_with_other_owner_phone_does_not_compare_phones(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Телефон заявки с телефоном владельца не сверяется: клиника заводится на другого человека."""
    lead = await _lead(public_client)
    owner_phone = _phone()
    assert owner_phone != lead["phone"]
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]

    resp = await _convert(
        admin_client,
        lead["id"],
        phone=owner_phone,
        type="clinic",
        name="Клиника по заявке",
        cityId=city,
        businessPhone="0700000000",
        seats=5,
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["type"] == "clinic"
    assert resp.json()["adminPhone"] == normalize_phone(owner_phone)
    row = await _row(_resources, lead["id"])
    assert row.converted_clinic_id == uuid.UUID(resp.json()["id"])
    assert row.phone == normalize_phone(lead["phone"])  # телефон заявки остался прежним
    assert not await _account_exists(_resources, lead["phone"])  # на номер лида аккаунта нет


async def test_convert_otp_application_marks_it_reviewed_and_keeps_its_fields(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Заявка через OTP конвертируется так же, как лид; её данные (компания, город) целы."""
    application = await _otp_application(public_client)
    admin_id = await _admin_id(_resources)

    resp = await _convert(admin_client, application["id"], phone=application["phone"])

    assert resp.status_code == 201, resp.text
    card = (await admin_client.get(f"{API}/admin/clinic-applications/{application['id']}")).json()
    assert card["convertedClinicId"] == resp.json()["id"]
    assert card["status"] == "reviewed"
    assert card["reviewedByAccountId"] == str(admin_id)
    assert card["phoneVerified"] is True
    assert card["companyName"] == "Дента+"
    assert card["doctorsCount"] == "6-10"
    assert card["cityName"]
    changed = await _audit(
        admin_client, action="clinic_application.status_changed", entity_id=application["id"]
    )
    assert [e["payload"]["reason"] for e in changed] == ["converted"]


async def test_convert_rejected_application_is_allowed_and_makes_it_reviewed(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Отклонённую заявку можно конвертировать (команда передумала): она становится ``reviewed``."""
    lead = await _lead(public_client)
    path = f"{API}/admin/clinic-applications/{lead['id']}/status"
    assert (await admin_client.patch(path, json={"status": "rejected"})).status_code == 200
    rejected_at = (await _row(_resources, lead["id"])).reviewed_at
    assert rejected_at is not None

    resp = await _convert(admin_client, lead["id"])

    assert resp.status_code == 201, resp.text
    clinic_id = resp.json()["id"]
    row = await _row(_resources, lead["id"])
    assert row.status is ClinicApplicationStatus.REVIEWED
    assert row.converted_clinic_id == uuid.UUID(clinic_id)
    assert row.reviewed_by_account_id == await _admin_id(_resources)
    assert row.reviewed_at is not None and row.reviewed_at > rejected_at
    # В аудите обе смены: ручное «отклонено» и автоматическое «рассмотрено» при заведении.
    payloads = [
        e["payload"]
        for e in await _audit(
            admin_client, action="clinic_application.status_changed", entity_id=lead["id"]
        )
    ]
    assert len(payloads) == 2
    assert {"status": "rejected"} in payloads
    assert {"status": "reviewed", "reason": "converted", "clinicId": clinic_id} in payloads


async def test_convert_application_already_reviewed_by_another_admin_keeps_reviewer_and_time(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Уже рассмотренную другим админом заявку конвертируем: рассмотревший и время не меняются.

    Клиника проставляется, но смены статуса нет — значит, и записи о ней в аудите нет
    (в журнале остаётся только исходная от второго админа).
    """
    other_id, other_headers = await _second_admin(_resources, public_client)
    lead = await _lead(public_client)
    reviewed = await public_client.patch(
        f"{API}/admin/clinic-applications/{lead['id']}/status",
        headers=other_headers,
        json={"status": "reviewed"},
    )
    assert reviewed.status_code == 200, reviewed.text
    before = await _row(_resources, lead["id"])
    assert before.reviewed_by_account_id == other_id

    resp = await _convert(admin_client, lead["id"])

    assert resp.status_code == 201, resp.text
    clinic_id = resp.json()["id"]
    row = await _row(_resources, lead["id"])
    assert row.converted_clinic_id == uuid.UUID(clinic_id)
    assert row.status is ClinicApplicationStatus.REVIEWED
    assert row.reviewed_by_account_id == other_id  # не затёрт вызвавшим админом
    assert row.reviewed_at == before.reviewed_at  # и время то же
    card = (await admin_client.get(f"{API}/admin/clinic-applications/{lead['id']}")).json()
    assert card["reviewedByName"] == "Второй Админ"
    changed = await _audit(
        admin_client, action="clinic_application.status_changed", entity_id=lead["id"]
    )
    assert [e["payload"] for e in changed] == [{"status": "reviewed"}]
    assert changed[0]["actorId"] == str(other_id)
    created = await _audit(admin_client, action="clinic.created", entity_id=clinic_id)
    assert created[0]["payload"] == {"type": "solo", "applicationId": lead["id"]}


# ── 3. Повторная конверсия ──────────────────────────────────────────────────


async def test_second_conversion_of_same_application_is_409_and_creates_nothing(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Повторный POST с тем же ``applicationId`` (другой телефон) → 409, второго клиента нет."""
    lead = await _lead(public_client)
    first = await _convert(admin_client, lead["id"])
    assert first.status_code == 201, first.text
    first_clinic = first.json()["id"]
    row_before = await _row(_resources, lead["id"])
    counts_before = await _counts(_resources)
    second_phone = _phone()

    second = await _convert(admin_client, lead["id"], phone=second_phone)

    assert second.status_code == 409, second.text
    body = second.json()
    assert body["error"] == "application_already_converted"
    assert body["message"] == "По этой заявке клиент уже заведён"
    assert body["clinicId"] == first_clinic  # подсказка: где уже заведён клиент
    assert await _counts(_resources) == counts_before
    assert not await _account_exists(_resources, second_phone)
    row = await _row(_resources, lead["id"])
    assert row.converted_clinic_id == uuid.UUID(first_clinic)
    assert row.status is ClinicApplicationStatus.REVIEWED
    assert row.reviewed_by_account_id == row_before.reviewed_by_account_id
    assert row.reviewed_at == row_before.reviewed_at
    # Аудит не размножился: одно создание клиента и одна смена статуса.
    created = [
        e
        for e in await _audit(admin_client, action="clinic.created")
        if e["payload"].get("applicationId") == lead["id"]
    ]
    assert len(created) == 1
    changed = await _audit(
        admin_client, action="clinic_application.status_changed", entity_id=lead["id"]
    )
    assert len(changed) == 1


async def test_repeat_with_same_owner_phone_reports_phone_already_used_first(
    admin_client: AsyncClient, public_client: AsyncClient
) -> None:
    """Повтор с тем же телефоном владельца отвечает ``phone_already_used`` (телефон — раньше).

    Порядок кодов — часть контракта для фронта: «номер занят» важнее «заявка уже
    сконвертирована», потому что проверяется до блокировки заявки.
    """
    lead = await _lead(public_client)
    first = await _convert(admin_client, lead["id"], phone=lead["phone"])
    assert first.status_code == 201, first.text

    again = await _convert(admin_client, lead["id"], phone=lead["phone"])

    assert again.status_code == 409, again.text
    assert again.json()["error"] == "phone_already_used"


async def test_reopening_converted_application_does_not_allow_second_conversion(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Критерий «уже заведён» — ``converted_clinic_id``, а не статус.

    Заявку вернули в ``pending`` вручную — клиника из карточки не пропадает, а второй
    клиент по ней по-прежнему не заводится.
    """
    lead = await _lead(public_client)
    first = await _convert(admin_client, lead["id"])
    assert first.status_code == 201, first.text
    clinic_id = first.json()["id"]
    reopened = await admin_client.patch(
        f"{API}/admin/clinic-applications/{lead['id']}/status", json={"status": "pending"}
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["convertedClinicId"] == clinic_id
    counts_before = await _counts(_resources)

    second = await _convert(admin_client, lead["id"])

    assert second.status_code == 409, second.text
    assert second.json()["error"] == "application_already_converted"
    assert second.json()["clinicId"] == clinic_id
    assert await _counts(_resources) == counts_before
    assert (await _row(_resources, lead["id"])).status is ClinicApplicationStatus.PENDING


async def test_application_of_deleted_clinic_can_be_converted_again(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Клинику по заявке удалили (FK ``ON DELETE SET NULL``) — заявку можно конвертировать снова.

    Статус при этом остаётся ``reviewed``, значит рассмотревший и время прежние, а новой
    записи о смене статуса в аудите нет.
    """
    lead = await _lead(public_client)
    first = await _convert(admin_client, lead["id"])
    assert first.status_code == 201, first.text
    first_row = await _row(_resources, lead["id"])
    async with _resources.factory() as session:
        await session.execute(delete(Clinic).where(Clinic.id == uuid.UUID(first.json()["id"])))
        await session.commit()
    assert (await _row(_resources, lead["id"])).converted_clinic_id is None

    second = await _convert(admin_client, lead["id"])

    assert second.status_code == 201, second.text
    row = await _row(_resources, lead["id"])
    assert row.converted_clinic_id == uuid.UUID(second.json()["id"])
    assert row.reviewed_by_account_id == first_row.reviewed_by_account_id
    assert row.reviewed_at == first_row.reviewed_at
    changed = await _audit(
        admin_client, action="clinic_application.status_changed", entity_id=lead["id"]
    )
    assert len(changed) == 1


# ── 4. Ошибки: ничего не создаётся, заявка не помечается ────────────────────


async def test_unknown_application_id_is_422_and_creates_nothing(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Несуществующий ``applicationId`` → 422 ``application_not_found``; ни клиента, ни аккаунта."""
    bystander = await _lead(public_client)
    counts_before = await _counts(_resources)
    owner_phone = _phone()

    resp = await _convert(admin_client, str(uuid.uuid4()), phone=owner_phone)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "application_not_found"
    assert resp.json()["message"] == "Заявка не найдена"
    assert (
        await _counts(_resources) == counts_before
    )  # ни clinic, ни account, ни membership, ни subscription
    assert not await _account_exists(_resources, owner_phone)
    _assert_untouched(await _row(_resources, bystander["id"]), ClinicApplicationStatus.PENDING)
    assert await _audit(admin_client, action="clinic.created") == []


async def test_non_uuid_application_id_is_422_validation_error(
    admin_client: AsyncClient, _resources: Resources
) -> None:
    """``applicationId`` не UUID → 422 ``validation_error`` с указанием поля; клиент не создан."""
    counts_before = await _counts(_resources)

    resp = await _convert(admin_client, "не-uuid")

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"] == "validation_error"
    assert any("applicationId" in e["loc"] for e in body["details"]), body
    assert await _counts(_resources) == counts_before


@pytest.mark.parametrize(
    "case", ["phone_taken", "city_not_found", "specialization_not_found", "invalid_body"]
)
async def test_provisioning_failure_with_application_id_leaves_application_untouched(
    admin_client: AsyncClient,
    public_client: AsyncClient,
    _resources: Resources,
    case: str,
) -> None:
    """Ошибка заведения по любой другой причине — заявка не помечена, ничего не создано.

    Проверяются разные места отказа: занятый телефон (до блокировки заявки), нет города и
    нет специализации (после проверок, но до создания), невалидное тело (до роута).
    """
    lead = await _lead(public_client)
    taken = _phone()
    if case == "phone_taken":
        assert (await _convert(admin_client, None, phone=taken)).status_code == 201
    counts_before = await _counts(_resources)
    audit_before = len(await _audit(admin_client, action="clinic.created"))

    if case == "phone_taken":
        resp = await _convert(admin_client, lead["id"], phone=taken)
        expected = (409, "phone_already_used")
    elif case == "city_not_found":
        resp = await _convert(
            admin_client,
            lead["id"],
            type="clinic",
            name="Клиника без города",
            cityId=str(uuid.uuid4()),
            businessPhone="0700000000",
            seats=5,
        )
        expected = (422, "city_not_found")
    elif case == "specialization_not_found":
        resp = await _convert(admin_client, lead["id"], specializationId=str(uuid.uuid4()))
        expected = (422, "specialization_not_found")
    else:
        resp = await _convert(admin_client, lead["id"], adminFullName="А")
        expected = (422, "validation_error")

    assert (resp.status_code, resp.json()["error"]) == expected, resp.text
    assert await _counts(_resources) == counts_before
    _assert_untouched(await _row(_resources, lead["id"]), ClinicApplicationStatus.PENDING)
    assert len(await _audit(admin_client, action="clinic.created")) == audit_before
    assert await _audit(admin_client, action="clinic_application.status_changed") == []


async def test_failure_after_client_creation_rolls_back_everything_and_releases_the_lock(
    admin_client: AsyncClient,
    public_client: AsyncClient,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой при пометке заявки откатывает и клиента: конверсия и заведение — одна транзакция.

    Затем блокировка заявки не «зависает»: повторный запрос (уже без сбоя) проходит и
    конвертирует ту же заявку.
    """
    lead = await _lead(public_client)
    counts_before = await _counts(_resources)
    owner_phone = _phone()

    async def boom(
        self: ClinicApplicationRepository,
        application_id: uuid.UUID,
        clinic_id: uuid.UUID,
        actor_id: uuid.UUID | None,
    ) -> ClinicApplicationStatus:
        raise RuntimeError("сбой посреди провижининга")

    with monkeypatch.context() as patch:
        patch.setattr(ClinicApplicationRepository, "mark_converted", boom)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": admin_client.headers["Authorization"]},
        ) as broken:
            failed = await _convert(broken, lead["id"], phone=owner_phone)

    assert failed.status_code == 500, failed.text
    assert await _counts(_resources) == counts_before  # клиент не остался «наполовину»
    assert not await _account_exists(_resources, owner_phone)
    _assert_untouched(await _row(_resources, lead["id"]), ClinicApplicationStatus.PENDING)

    retry = await asyncio.wait_for(_convert(admin_client, lead["id"], phone=owner_phone), 10)
    assert retry.status_code == 201, retry.text
    assert (await _row(_resources, lead["id"])).converted_clinic_id == uuid.UUID(retry.json()["id"])


# ── 5. Права ────────────────────────────────────────────────────────────────


async def test_convert_without_token_is_401_and_application_untouched(
    public_client: AsyncClient, _resources: Resources
) -> None:
    """Без токена ручка закрыта (401); заявка и клиенты не меняются."""
    lead = await _lead(public_client)
    counts_before = await _counts(_resources)

    resp = await _convert(public_client, lead["id"])

    assert resp.status_code == 401, resp.text
    assert await _counts(_resources) == counts_before
    _assert_untouched(await _row(_resources, lead["id"]), ClinicApplicationStatus.PENDING)


async def test_clinic_owner_cannot_convert_application_403(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Владелец клиники (не из команды Skrezo) не может заводить клиентов из заявок: 403."""
    owner_phone = _phone()
    assert (await _convert(admin_client, None, phone=owner_phone)).status_code == 201
    headers = await otp_login(public_client, owner_phone)
    lead = await _lead(public_client)
    counts_before = await _counts(_resources)

    resp = await public_client.post(
        f"{API}/admin/clients",
        headers=headers,
        json={
            "type": "solo",
            "adminFullName": "Самозванец",
            "adminPhone": _phone(),
            "applicationId": lead["id"],
        },
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "not_platform_admin"
    assert await _counts(_resources) == counts_before
    _assert_untouched(await _row(_resources, lead["id"]), ClinicApplicationStatus.PENDING)


# ── 6. Гонка ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("attempt", range(3))
async def test_concurrent_conversions_of_same_application_create_exactly_one_client(
    admin_client: AsyncClient,
    public_client: AsyncClient,
    _resources: Resources,
    attempt: int,
) -> None:
    """Два одновременных POST с одним ``applicationId`` и разными телефонами: один 201, один 409.

    Проигравший не создаёт ни клиники, ни аккаунта, ни подписки; в 409 — клиника
    победителя. Прогоняется трижды, чтобы не полагаться на удачный порядок.
    """
    lead = await _lead(public_client)
    counts_before = await _counts(_resources)
    phones = [_phone(), _phone()]

    responses = await asyncio.gather(
        *(_convert(admin_client, lead["id"], phone=phone) for phone in phones)
    )

    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    winner = next(r for r in responses if r.status_code == 201)
    loser = next(r for r in responses if r.status_code == 409)
    assert loser.json()["error"] == "application_already_converted"
    assert loser.json()["clinicId"] == winner.json()["id"]

    after = await _counts(_resources)
    assert after.clinics == counts_before.clinics + 1
    assert after.accounts == counts_before.accounts + 1
    assert after.memberships == counts_before.memberships + 1
    assert after.subscriptions == counts_before.subscriptions + 1
    assert after.clinic_roles > counts_before.clinic_roles  # роли — только победителя
    winner_phone = winner.json()["adminPhone"]
    loser_phone = next(p for p in phones if normalize_phone(p) != winner_phone)
    assert await _account_exists(_resources, winner_phone)
    assert not await _account_exists(_resources, loser_phone)

    row = await _row(_resources, lead["id"])
    assert row.converted_clinic_id == uuid.UUID(winner.json()["id"])
    assert row.status is ClinicApplicationStatus.REVIEWED
    created = [
        e
        for e in await _audit(admin_client, action="clinic.created")
        if e["payload"].get("applicationId") == lead["id"]
    ]
    assert len(created) == 1
    assert (
        len(
            await _audit(
                admin_client, action="clinic_application.status_changed", entity_id=lead["id"]
            )
        )
        == 1
    )


async def test_request_waits_for_row_lock_and_sees_conversion_made_by_the_lock_holder(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Блокировка заявки: запрос ждёт чужой ``FOR UPDATE`` и перечитывает строку после ожидания.

    «Победитель» держит блокировку заявки, запрос встаёт в очередь (не завершается),
    победитель коммитит конверсию — запрос просыпается и честно отвечает 409 с клиникой
    победителя, ничего не создав. Ловит отсутствие ``FOR UPDATE``; ``populate_existing``
    этот тест не проверяет — строка заявки загружается в сессию запроса впервые.
    """
    lead = await _lead(public_client)
    winner_clinic = await _convert(admin_client, None)
    assert winner_clinic.status_code == 201, winner_clinic.text
    counts_before = await _counts(_resources)
    loser_phone = _phone()

    async with _resources.factory() as holder:
        locked = await ClinicApplicationRepository(holder).lock_for_conversion(
            uuid.UUID(lead["id"])
        )
        assert locked is not None
        waiting = asyncio.create_task(_convert(admin_client, lead["id"], phone=loser_phone))
        await asyncio.sleep(0.7)
        assert not waiting.done(), "запрос не дождался блокировки заявки"

        await holder.execute(
            update(ClinicApplication)
            .where(ClinicApplication.id == uuid.UUID(lead["id"]))
            .values(converted_clinic_id=uuid.UUID(winner_clinic.json()["id"]))
        )
        await holder.commit()

    resp = await asyncio.wait_for(waiting, 10)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"] == "application_already_converted"
    assert resp.json()["clinicId"] == winner_clinic.json()["id"]
    assert await _counts(_resources) == counts_before
    assert not await _account_exists(_resources, loser_phone)


async def test_request_waiting_for_row_lock_proceeds_when_holder_rolls_back(
    admin_client: AsyncClient, public_client: AsyncClient, _resources: Resources
) -> None:
    """Если держатель блокировки откатился (конверсии не было), ожидавший запрос проходит: 201."""
    lead = await _lead(public_client)
    owner_phone = _phone()

    async with _resources.factory() as holder:
        assert await ClinicApplicationRepository(holder).lock_for_conversion(uuid.UUID(lead["id"]))
        waiting = asyncio.create_task(_convert(admin_client, lead["id"], phone=owner_phone))
        await asyncio.sleep(0.7)
        assert not waiting.done(), "запрос не дождался блокировки заявки"
        await holder.rollback()

    resp = await asyncio.wait_for(waiting, 10)
    assert resp.status_code == 201, resp.text
    row = await _row(_resources, lead["id"])
    assert row.converted_clinic_id == uuid.UUID(resp.json()["id"])
    assert row.status is ClinicApplicationStatus.REVIEWED


# ── 7. После конверсии: новые лиды по тому же номеру ───────────────────────


@pytest.mark.parametrize(
    ("owner_is_lead_phone", "expected_status", "expected_error"),
    [(False, 201, None), (True, 409, "already_client")],
    ids=["владелец_с_другим_номером", "владелец_с_номером_лида"],
)
async def test_new_lead_on_converted_phone_depends_on_whether_an_account_exists(
    admin_client: AsyncClient,
    public_client: AsyncClient,
    _resources: Resources,
    owner_is_lead_phone: bool,
    expected_status: int,
    expected_error: str | None,
) -> None:
    """После конверсии лида (``reviewed``) номер лида освобождён от дедупа «открытая заявка».

    * владелец клиента — другой человек (другой номер): на номер лида аккаунта нет,
      новый лид принимается (201), старая заявка остаётся сконвертированной;
    * владелец — тот же номер: аккаунт есть, ответ ``already_client`` (форма предложит войти).
    """
    lead = await _lead(public_client)
    owner_phone = lead["phone"] if owner_is_lead_phone else _phone()
    assert (await _convert(admin_client, lead["id"], phone=owner_phone)).status_code == 201

    repeat = await public_client.post(
        f"{API}/leads", json={"fullName": "Снова оставил", "phone": lead["phone"]}
    )

    assert repeat.status_code == expected_status, repeat.text
    if expected_error is not None:
        assert repeat.json()["error"] == expected_error
        return
    new_id = repeat.json()["id"]
    assert new_id != lead["id"]
    fresh = (await admin_client.get(f"{API}/admin/clinic-applications/{new_id}")).json()
    assert fresh["status"] == "pending"
    assert fresh["convertedClinicId"] is None
    assert (await _row(_resources, lead["id"])).converted_clinic_id is not None


# ── 8. Дашборд ──────────────────────────────────────────────────────────────


async def test_dashboard_conversion_counts_applications_converted_through_the_endpoint(
    admin_client: AsyncClient, public_client: AsyncClient
) -> None:
    """Конверсия на дашборде растёт от настоящей ручки; отказы и повторы её не двигают."""
    leads = [await _lead(public_client) for _ in range(3)]

    async def conversion() -> dict[str, Any]:
        summary = await admin_client.get(f"{API}/admin/dashboard/summary")
        assert summary.status_code == 200, summary.text
        block: dict[str, Any] = summary.json()["conversion"]
        return block

    assert await conversion() == {"pct": 0.0, "converted": 0, "leads": 3}

    assert (await _convert(admin_client, leads[0]["id"])).status_code == 201
    assert await conversion() == {"pct": 33.3, "converted": 1, "leads": 3}

    # Повтор по той же заявке и заявка-призрак ничего не добавляют.
    assert (await _convert(admin_client, leads[0]["id"])).status_code == 409
    assert (await _convert(admin_client, str(uuid.uuid4()))).status_code == 422
    assert await conversion() == {"pct": 33.3, "converted": 1, "leads": 3}

    assert (await _convert(admin_client, leads[1]["id"])).status_code == 201
    assert await conversion() == {"pct": 66.7, "converted": 2, "leads": 3}


async def test_client_created_without_application_does_not_raise_dashboard_conversion(
    admin_client: AsyncClient, public_client: AsyncClient
) -> None:
    """Клиент, заведённый без заявки, конверсию «заявки → клиенты» не двигает."""
    await _lead(public_client)
    assert (await _convert(admin_client, None)).status_code == 201

    body = (await admin_client.get(f"{API}/admin/dashboard/summary")).json()

    assert body["conversion"] == {"pct": 0.0, "converted": 0, "leads": 1}


# ── 9. Контракт OpenAPI ─────────────────────────────────────────────────────


@pytest.fixture
def schema() -> dict[str, Any]:
    spec: dict[str, Any] = app.openapi()
    return spec


def _post_clients(schema: dict[str, Any]) -> dict[str, Any]:
    op: dict[str, Any] = schema["paths"]["/api/v1/admin/clients"]["post"]
    return op


def test_openapi_create_account_request_has_optional_documented_application_id(
    schema: dict[str, Any],
) -> None:
    """В теле ``POST /admin/clients`` ``applicationId`` необязателен: UUID/null, с описанием."""
    request = schema["components"]["schemas"]["CreateAccountRequest"]
    prop = request["properties"]["applicationId"]

    assert "applicationId" not in request["required"]
    assert {"type": "string", "format": "uuid"} in prop["anyOf"]
    assert {"type": "null"} in prop["anyOf"]
    assert "clinic-applications" in prop["description"]
    assert prop["examples"]
    # Обязательные поля не изменились: applicationId не «потянул» за собой другие.
    assert set(request["required"]) == {"type", "adminFullName", "adminPhone"}


def test_openapi_create_account_documents_new_errors_and_keeps_phone_already_used(
    schema: dict[str, Any],
) -> None:
    """409/422 описаны именованными примерами; ``phone_already_used`` не потерян."""
    responses = _post_clients(schema)["responses"]

    conflict = responses["409"]["content"]["application/json"]["examples"]
    assert {"phone_already_used", "application_already_converted"} <= set(conflict)
    assert conflict["phone_already_used"]["value"]["error"] == "phone_already_used"
    converted = conflict["application_already_converted"]["value"]
    assert converted["error"] == "application_already_converted"
    assert "clinicId" in converted

    unprocessable = responses["422"]["content"]["application/json"]["examples"]
    assert "application_not_found" in unprocessable
    assert unprocessable["application_not_found"]["value"]["error"] == "application_not_found"
    assert "201" in responses  # успешный ответ не потерялся


def test_openapi_create_account_description_explains_the_conversion(
    schema: dict[str, Any],
) -> None:
    """Описание ручки объясняет, что происходит с заявкой, про телефон и про ``rejected``."""
    description = _post_clients(schema)["description"]

    assert "applicationId" in description
    assert "convertedClinicId" in description
    assert "reviewed" in description
    assert "rejected" in description
    assert "не сверяется" in description


def test_openapi_application_item_converted_clinic_id_is_required_and_nullable(
    schema: dict[str, Any],
) -> None:
    """``convertedClinicId``: обязательный ключ, допускающий ``null`` (``string | null``)."""
    item = schema["components"]["schemas"]["ApplicationItem"]
    prop = item["properties"]["convertedClinicId"]

    assert "convertedClinicId" in item["required"]
    assert {"type": "string", "format": "uuid"} in prop["anyOf"]
    assert {"type": "null"} in prop["anyOf"]
    assert "default" not in prop
    assert prop.get("description")
    # В исходных примерах модели ключ есть в обоих: null (клиента не завели) и uuid
    # (заявка сконвертирована). В отдаваемую схему OpenAPI null-значения примеров не
    # попадают (так и у остальных nullable-полей), поэтому там остаётся только uuid.
    extra = cast(dict[str, Any], ApplicationItem.model_config["json_schema_extra"])
    source_values = [example["convertedClinicId"] for example in extra["examples"]]
    assert None in source_values
    assert any(isinstance(v, str) for v in source_values)
    published = [e["convertedClinicId"] for e in item["examples"] if "convertedClinicId" in e]
    assert len(published) == 1
    uuid.UUID(published[0])


def test_openapi_create_lead_request_is_unchanged(schema: dict[str, Any]) -> None:
    """Соседняя схема не задета: у ``CreateLeadRequest`` обязательны лишь имя и телефон."""
    request = schema["components"]["schemas"]["CreateLeadRequest"]

    assert set(request["required"]) == {"fullName", "phone"}
    for name in ("comment", "source", "clinicName", "doctorsCountRange"):
        assert name not in request["required"], name
        assert {"type": "null"} in request["properties"][name]["anyOf"], name
    assert "convertedClinicId" not in request["properties"]
