"""Демо-клиника: признак у клиники, сброс её дня и границы того, что он удаляет.

Главное здесь — чего сброс НЕ трогает: данных клиники до включения признака и
карточек пациентов, которые нужны другой клинике (пациент глобальный).
"""

from __future__ import annotations

import itertools
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

import app.jobs.tasks.demo as demo_tasks
import app.modules.demo.application.services.demo_reset_service as reset_module
from app.core.config import settings
from app.modules.ai.application.patient_reminders import PatientReminderService
from app.modules.ai.application.visits import VisitReminderService
from app.modules.demo.domain.day_plan import DAY_PLAN, PEOPLE, demo_phone, is_reset_due
from app.modules.push.application.unconfirmed_service import UnconfirmedVisitService
from app.shared.db.enums import AiConversationStatus, AiMessageRole, AppointmentStatus
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.audit_log import AuditLog
from app.shared.db.models.cash_payment import CashPayment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_demo import ClinicDemo
from app.shared.db.models.event import Event
from app.shared.db.models.patient import Patient
from app.shared.db.models.patient_file import PatientFile
from app.shared.domain.value_objects import normalize_phone
from tests.api.conftest import Ctx
from tests.api.test_visit_reminders_api import _book, _enable, _FakeWa, _run, _template
from tests.conftest import API, Resources, otp_login, own_membership_id

_BISHKEK = ZoneInfo("Asia/Bishkek")
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
_seq = itertools.count(1)


def _phone() -> str:
    return f"070095{next(_seq):04d}"


async def _add_doctor(client: AsyncClient, ctx: Ctx, name: str) -> tuple[str, str]:
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    phone = _phone()
    added = await client.post(
        f"{API}/clinic/staff",
        headers=ctx.owner_headers,
        json={"fullName": name, "phone": phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    return str(added.json()["membershipId"]), phone


async def _set_demo(
    admin_client: AsyncClient, ctx: Ctx, demo: bool = True, name: str | None = "клиника ии "
) -> Any:
    return await admin_client.put(
        f"{API}/admin/clients/{ctx.clinic_id}/demo", json={"demo": demo, "confirmName": name}
    )


async def _enabled(admin_client: AsyncClient, client: AsyncClient, ctx: Ctx) -> None:
    """Демо-клиника с тремя врачами и прайсом (услугу заводит ``ctx``)."""
    for name in ("Врач Первый", "Врач Второй", "Врач Третий"):
        await _add_doctor(client, ctx, name)
    resp = await _set_demo(admin_client, ctx)
    assert resp.status_code == 200, resp.text


async def _reset(client: AsyncClient, ctx: Ctx) -> Any:
    resp = await client.post(f"{API}/clinic/demo/reset", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _other_clinic(admin_client: AsyncClient, client: AsyncClient, name: str) -> Ctx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Соседний",
            "adminPhone": phone,
            "name": name,
            "cityId": city,
            "businessPhone": "0700000002",
            "seats": 3,
        },
    )
    assert created.status_code == 201, created.text
    return Ctx(str(created.json()["id"]), await otp_login(client, phone), "")


async def _book_patient(
    client: AsyncClient, ctx: Ctx, res: Resources, patient_id: str, when: datetime
) -> str:
    """Записать существующего пациента мимо рабочих часов — тесту нужна связь."""
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    async with res.factory() as session:
        appointment = Appointment(
            clinic_id=uuid.UUID(ctx.clinic_id),
            patient_id=uuid.UUID(patient_id),
            doctor_membership_id=uuid.UUID(doctor_id),
            scheduled_at=when,
            duration_min=30,
            status=AppointmentStatus.SCHEDULED,
        )
        session.add(appointment)
        await session.commit()
        return str(appointment.id)


async def _create_patient(client: AsyncClient, ctx: Ctx, phone: str) -> str:
    resp = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.owner_headers,
        json={"phone": phone, "firstName": "Гость", "lastName": "Показа"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _exists(res: Resources, model: Any, row_id: str | uuid.UUID) -> bool:
    async with res.factory() as session:
        return await session.get(model, uuid.UUID(str(row_id))) is not None


async def _visits(res: Resources, ctx: Ctx) -> list[Appointment]:
    async with res.factory() as session:
        rows = await session.execute(
            select(Appointment)
            .where(Appointment.clinic_id == uuid.UUID(ctx.clinic_id))
            .order_by(Appointment.scheduled_at)
        )
        return list(rows.scalars())


# ── Чистые правила ──────────────────────────────────────────────────────────


def test_plan_never_overlaps_and_matches_people() -> None:
    spans = sorted((p.offset_minutes, p.offset_minutes + p.duration_minutes) for p in DAY_PLAN)
    assert all(left[1] <= right[0] for left, right in itertools.pairwise(spans))
    assert len(PEOPLE) == len(DAY_PLAN)
    assert Counter(p.status.value for p in DAY_PLAN) == {
        "completed": 2,
        "no_show": 1,
        "in_progress": 1,
        "scheduled": 2,
        "confirmed": 1,
    }


def test_demo_phones_are_well_formed() -> None:
    """Формат проходит проверку телефонов — карточку можно править в кабинете."""
    for index in (1, 7, 99999):
        assert normalize_phone(demo_phone(index)) == demo_phone(index)


@pytest.mark.parametrize(
    ("utc_hour", "minute", "reset_hours_ago", "due"),
    [
        (0, 0, None, True),  # 06:00 по Бишкеку
        (0, 20, 24, True),  # вчерашний сброс не считается
        (0, 20, 0.2, False),  # уже собран в этот же час
        (23, 59, None, False),  # 05:59
        (4, 0, None, False),  # 10:00 — посреди показа не догоняем
    ],
)
def test_reset_is_due_only_at_six_in_clinic_time(
    utc_hour: int, minute: int, reset_hours_ago: float | None, due: bool
) -> None:
    now = datetime(2027, 3, 10, utc_hour, minute, tzinfo=UTC)
    reset_at = None if reset_hours_ago is None else now - timedelta(hours=reset_hours_ago)
    assert is_reset_due(now, "Asia/Bishkek", reset_at, 6) is due


# ── Признак ─────────────────────────────────────────────────────────────────


async def test_demo_flag_is_confirmed_by_name_and_visible(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    for wrong in (None, "", "Другая клиника"):
        refused = await _set_demo(admin_client, ctx, name=wrong)
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"] == "confirm_name_mismatch"
    shown = await client.get(f"{API}/clinic/settings", headers=ctx.owner_headers)
    assert shown.json()["demo"] is False

    enabled = await _set_demo(admin_client, ctx)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["demo"] is True
    assert enabled.json()["since"] and enabled.json()["resetAt"] is None

    shown = await client.get(f"{API}/clinic/settings", headers=ctx.owner_headers)
    assert shown.json()["demo"] is True
    me = await client.get(f"{API}/auth/me", headers=ctx.owner_headers)
    assert me.json()["activeClinic"]["demo"] is True
    card = await admin_client.get(f"{API}/admin/clients/{ctx.clinic_id}")
    assert card.json()["demo"] is True

    # Повторное включение границу не сдвигает.
    again = await _set_demo(admin_client, ctx)
    assert again.json()["since"] == enabled.json()["since"]
    # Снять признак можно без названия; включённый снова — граница новая.
    off = await _set_demo(admin_client, ctx, demo=False, name=None)
    assert off.status_code == 200 and off.json()["demo"] is False
    on = await _set_demo(admin_client, ctx)
    assert on.json()["since"] > enabled.json()["since"]

    # Клиника включить себе демо не может — только команда Skrezo.
    denied = await client.put(
        f"{API}/admin/clients/{ctx.clinic_id}/demo",
        headers=ctx.owner_headers,
        json={"demo": True, "confirmName": "Клиника ИИ"},
    )
    assert denied.status_code == 403
    async with _resources.factory() as session:
        audit = (
            await session.execute(select(func.count()).where(AuditLog.action == "client.demo"))
        ).scalar_one()
    assert audit == 4


async def test_reset_is_only_for_demo_clinic_and_its_managers(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    appointment_id = await _book(client, ctx, phone=_phone(), first_name="Настоящий")
    refused = await client.post(f"{API}/clinic/demo/reset", headers=ctx.owner_headers)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"] == "not_demo_clinic"
    assert await _exists(_resources, Appointment, appointment_id)

    _, doctor_phone = await _add_doctor(client, ctx, "Врач Показа")
    await _set_demo(admin_client, ctx)
    worker = await otp_login(client, doctor_phone)
    denied = await client.post(f"{API}/clinic/demo/reset", headers=worker)
    assert denied.status_code == 403
    assert denied.json()["error"] != "not_demo_clinic"


async def test_clinic_without_doctors_keeps_its_data(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Собрать не из чего — показанное остаётся, а не пустая клиника перед показом."""
    await _set_demo(admin_client, ctx)
    patient_id = await _create_patient(client, ctx, _phone())
    resp = await client.post(f"{API}/clinic/demo/reset", headers=ctx.owner_headers)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "demo_not_ready"
    assert resp.json()["doctors"] == 0 and resp.json()["services"] == 1
    assert await _exists(_resources, Patient, patient_id)


# ── Сборка дня ──────────────────────────────────────────────────────────────


async def test_reset_builds_the_day_from_now(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    await _enabled(admin_client, client, ctx)
    before = datetime.now(UTC)
    result = await _reset(client, ctx)
    assert result["created"] == {"patients": 7, "appointments": 7}
    assert result["deleted"] == {
        "patients": 0,
        "appointments": 0,
        "payments": 0,
        "conversations": 0,
    }

    visits = await _visits(_resources, ctx)
    assert Counter(v.status.value for v in visits) == {
        "completed": 2,
        "no_show": 1,
        "in_progress": 1,
        "scheduled": 2,
        "confirmed": 1,
    }
    assert len({v.doctor_membership_id for v in visits}) == 3
    assert all(v.doctor_membership_id is not None for v in visits)
    for visit in visits:
        assert visit.scheduled_at is not None
        past = visit.status.value in ("completed", "no_show", "in_progress")
        assert (visit.scheduled_at < before) is past
        # «Задним числом»: иначе правило свежей записи прячет «не подтвердил».
        assert visit.created_at < visit.scheduled_at - timedelta(hours=2)
    no_show = next(v for v in visits if v.status is AppointmentStatus.NO_SHOW)
    assert no_show.scheduled_at is not None
    assert timedelta(minutes=55) < before - no_show.scheduled_at < timedelta(minutes=70)

    # Долг ровно один: второй завершённый приём оплачен полностью.
    debts = await client.get(f"{API}/clinic/cash/debts", headers=ctx.owner_headers)
    assert debts.status_code == 200, debts.text
    rows = debts.json()["items"]
    assert len(rows) == 1
    assert rows[0]["outstandingTiyin"] == 125000  # услуга за 250000, оплачена половина

    # Пациенты видны кабинету обычным списком и карточкой.
    listed = await client.get(f"{API}/clinic/patients", headers=ctx.owner_headers)
    assert listed.status_code == 200, listed.text
    async with _resources.factory() as session:
        phones = (await session.execute(select(Patient.phone))).scalars().all()
    assert len(phones) == 7 and all(p.startswith("+9963120") for p in phones)

    settings_row = await client.get(f"{API}/clinic/settings", headers=ctx.owner_headers)
    assert settings_row.json()["demo"] is True


async def test_unconfirmed_push_works_in_the_demo_day(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Ближайшая неподтверждённая запись поднимается сканом — на показе её и ждут."""
    await _enabled(admin_client, client, ctx)
    await _reset(client, ctx)
    async with _resources.factory() as session:
        assert await UnconfirmedVisitService(session).run() == 1


async def test_single_doctor_gets_the_whole_day(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    await _add_doctor(client, ctx, "Врач Единственный")
    await _set_demo(admin_client, ctx)
    await _reset(client, ctx)
    visits = await _visits(_resources, ctx)
    assert len(visits) == 7
    assert len({v.doctor_membership_id for v in visits}) == 1
    assert visits[0].doctor_membership_id is not None


async def test_day_goes_around_old_bookings_of_the_clinic(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Записи до включения демо остаются, и день собирается, обходя их."""
    doctor_id, _ = await _add_doctor(client, ctx, "Врач Занятый")
    patient_id = await _create_patient(client, ctx, _phone())
    async with _resources.factory() as session:
        old = Appointment(
            clinic_id=uuid.UUID(ctx.clinic_id),
            patient_id=uuid.UUID(patient_id),
            doctor_membership_id=uuid.UUID(doctor_id),
            scheduled_at=datetime.now(UTC) - timedelta(hours=6),
            duration_min=12 * 60,
            status=AppointmentStatus.CONFIRMED,
        )
        session.add(old)
        await session.commit()
        old_id = old.id
    await _set_demo(admin_client, ctx)
    await _add_doctor(client, ctx, "Врач Свободный")
    await _reset(client, ctx)

    visits = [v for v in await _visits(_resources, ctx) if v.id != old_id]
    assert len(visits) == 7
    assert doctor_id not in {str(v.doctor_membership_id) for v in visits}
    assert await _exists(_resources, Appointment, old_id)


# ── Что удаляется и что нет ─────────────────────────────────────────────────


async def test_second_reset_removes_shown_data_and_does_not_accumulate(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    await _enabled(admin_client, client, ctx)
    await _reset(client, ctx)
    first = {v.id for v in await _visits(_resources, ctx)}

    # Показ: завели пациента, записали на завтра, он писал в WhatsApp.
    shown_id = await _book(client, ctx, phone="0700900500", first_name="Клиент")
    async with _resources.factory() as session:
        conversation = AiConversation(clinic_id=uuid.UUID(ctx.clinic_id), wa_phone="+996700900500")
        session.add(conversation)
        await session.flush()
        session.add(
            AiMessage(
                conversation_id=conversation.id,
                clinic_id=uuid.UUID(ctx.clinic_id),
                role=AiMessageRole.PATIENT,
                body="Здравствуйте",
            )
        )
        await session.commit()
        conversation_id = conversation.id

    result = await _reset(client, ctx)
    assert result["deleted"] == {
        "patients": 8,  # семь выдуманных и гость показа
        "appointments": 8,
        "payments": 2,
        "conversations": 1,
    }
    assert not await _exists(_resources, Appointment, shown_id)
    assert not await _exists(_resources, AiConversation, conversation_id)
    visits = await _visits(_resources, ctx)
    assert len(visits) == 7 and first.isdisjoint({v.id for v in visits})
    # Завтра свободно: запись показа ушла вместе с ним.
    tomorrow = (datetime.now(UTC).astimezone(_BISHKEK) + timedelta(days=1)).date()
    morning = [
        v
        for v in visits
        if v.scheduled_at is not None
        and v.scheduled_at.astimezone(_BISHKEK).date() == tomorrow
        and 6 <= v.scheduled_at.astimezone(_BISHKEK).hour < 13
    ]
    assert morning == []
    async with _resources.factory() as session:
        patients = (await session.execute(select(func.count(Patient.id)))).scalar_one()
        payments = (await session.execute(select(func.count(CashPayment.id)))).scalar_one()
        dangling = (
            await session.execute(
                select(func.count()).where(Event.entity_id == uuid.UUID(shown_id))
            )
        ).scalar_one()
    assert (patients, payments, dangling) == (7, 2, 0)


async def test_data_before_the_flag_is_never_touched(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    old_visit = await _book(client, ctx, phone=_phone(), first_name="Старый")
    async with _resources.factory() as session:
        visit = await session.get(Appointment, uuid.UUID(old_visit))
        assert visit is not None
        old_patient = visit.patient_id
        conversation = AiConversation(
            clinic_id=uuid.UUID(ctx.clinic_id),
            wa_phone="+996700900777",
            patient_id=old_patient,
            status=AiConversationStatus.ESCALATED,
        )
        session.add(conversation)
        await session.flush()
        session.add(
            AiMessage(
                conversation_id=conversation.id,
                clinic_id=uuid.UUID(ctx.clinic_id),
                role=AiMessageRole.PATIENT,
                body="До показов",
            )
        )
        await session.commit()
        conversation_id = conversation.id

    await _enabled(admin_client, client, ctx)
    # На показе в старый диалог дописали и приняли оплату по старой записи.
    async with _resources.factory() as session:
        chat = await session.get(AiConversation, conversation_id)
        assert chat is not None
        chat.last_msg_at = datetime.now(UTC)
        session.add(
            AiMessage(
                conversation_id=conversation_id,
                clinic_id=uuid.UUID(ctx.clinic_id),
                role=AiMessageRole.PATIENT,
                body="На показе",
            )
        )
        await session.commit()
    result = await _reset(client, ctx)
    assert result["deleted"]["appointments"] == 0
    assert result["deleted"]["patients"] == 0
    assert result["deleted"]["conversations"] == 0

    assert await _exists(_resources, Appointment, old_visit)
    assert await _exists(_resources, Patient, old_patient)
    async with _resources.factory() as session:
        chat = await session.get(AiConversation, conversation_id)
        assert chat is not None and chat.status is AiConversationStatus.CLOSED
        bodies = (
            (
                await session.execute(
                    select(AiMessage.body).where(AiMessage.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )
    assert bodies == ["До показов"]


async def test_patients_needed_by_other_clinics_survive(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Пациент глобальный: карточку другой клиники демо не удаляет ни в одну сторону."""
    await _enabled(admin_client, client, ctx)
    other = await _other_clinic(admin_client, client, "Соседняя клиника")
    soon = datetime.now(UTC) + timedelta(days=3)

    # Завели в демо, потом он записался в настоящую клинику.
    moved = await _create_patient(client, ctx, _phone())
    demo_visit = await _book_patient(client, ctx, _resources, moved, soon)
    real_visit = await _book_patient(client, other, _resources, moved, soon)
    # Завела настоящая клиника, на показе его записали и в демо.
    foreign = await _create_patient(client, other, _phone())
    guest_visit = await _book_patient(client, ctx, _resources, foreign, soon + timedelta(hours=2))

    await _reset(client, ctx)
    assert not await _exists(_resources, Appointment, demo_visit)
    assert not await _exists(_resources, Appointment, guest_visit)
    assert await _exists(_resources, Patient, moved)
    assert await _exists(_resources, Patient, foreign)
    assert await _exists(_resources, Appointment, real_visit)
    # Карточка соседа без единой записи — тоже не наша, хоть и «ничья» по связям.
    lonely = await _create_patient(client, other, _phone())
    await _reset(client, ctx)
    assert await _exists(_resources, Patient, lonely)


async def test_patient_created_by_the_assistant_is_removed(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    await _enabled(admin_client, client, ctx)
    async with _resources.factory() as session:
        patient = Patient(phone="+996700900888", first_name="Со слов", source="assistant")
        stranger = Patient(phone="+996700900889", first_name="Чужой", source="assistant")
        session.add_all([patient, stranger])
        await session.flush()
        session.add(
            AiConversation(
                clinic_id=uuid.UUID(ctx.clinic_id),
                wa_phone="+996700900888",
                patient_id=patient.id,
            )
        )
        await session.commit()
        patient_id, stranger_id = patient.id, stranger.id
    await _reset(client, ctx)
    assert not await _exists(_resources, Patient, patient_id)
    assert await _exists(_resources, Patient, stranger_id)


async def test_files_uploaded_during_the_show_leave_the_disk(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "storage_root", str(tmp_path / "storage"))
    await _enabled(admin_client, client, ctx)
    appointment_id = await _book(client, ctx, phone=_phone(), first_name="Снимок")
    async with _resources.factory() as session:
        visit = await session.get(Appointment, uuid.UUID(appointment_id))
        assert visit is not None
        patient_id = visit.patient_id
    uploaded = await client.post(
        f"{API}/clinic/patients/{patient_id}/files",
        headers=ctx.owner_headers,
        files={"file": ("photo.jpg", _JPEG, "image/jpeg")},
    )
    assert uploaded.status_code == 201, uploaded.text
    stored = [p for p in (tmp_path / "storage").rglob("*") if p.is_file()]
    assert len(stored) == 1

    await _reset(client, ctx)
    assert not any(p.is_file() for p in (tmp_path / "storage").rglob("*"))
    async with _resources.factory() as session:
        files = (await session.execute(select(func.count(PatientFile.id)))).scalar_one()
    assert files == 0


async def test_data_added_while_the_flag_was_off_survives(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    await _enabled(admin_client, client, ctx)
    await _reset(client, ctx)
    seeded = {v.id for v in await _visits(_resources, ctx)}
    await _set_demo(admin_client, ctx, demo=False, name=None)
    kept = await _book(client, ctx, phone=_phone(), first_name="Настоящий")
    await _set_demo(admin_client, ctx)
    await _reset(client, ctx)
    assert await _exists(_resources, Appointment, kept)
    # А собранное прошлым сбросом помнится списком и уходит.
    assert seeded.isdisjoint({v.id for v in await _visits(_resources, ctx)})


async def test_two_demo_clinics_do_not_share_phones(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    await _enabled(admin_client, client, ctx)
    second = await _other_clinic(admin_client, client, "Демо вторая")
    await _add_doctor(client, second, "Врач Второй Демо")
    service = await client.post(
        f"{API}/clinic/services",
        headers=second.owner_headers,
        json={"name": "Осмотр", "priceTiyin": 100000},
    )
    assert service.status_code == 201, service.text
    assert (await _set_demo(admin_client, second, name="Демо вторая")).status_code == 200

    await _reset(client, ctx)
    await _reset(client, second)
    assert len(await _visits(_resources, ctx)) == 7
    assert len(await _visits(_resources, second)) == 7
    async with _resources.factory() as session:
        patients = (await session.execute(select(func.count(Patient.id)))).scalar_one()
    assert patients == 14


async def test_failed_build_rolls_back_the_purge(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Удаление и сборка — одна транзакция: сбой не оставляет пустую клинику."""
    await _enabled(admin_client, client, ctx)
    await _reset(client, ctx)
    before = {v.id for v in await _visits(_resources, ctx)}

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("сборка упала")

    monkeypatch.setattr(reset_module.DemoResetService, "_add_visits", _boom)
    async with _resources.factory() as session:
        with pytest.raises(RuntimeError):
            await reset_module.build_demo_reset_service(session).reset(uuid.UUID(ctx.clinic_id))
    assert {v.id for v in await _visits(_resources, ctx)} == before


# ── Расписание ──────────────────────────────────────────────────────────────


async def _tick(monkeypatch: pytest.MonkeyPatch, res: Resources, now: datetime) -> str:
    monkeypatch.setattr(demo_tasks, "session_factory", res.factory)
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory", res.factory
    )
    return await demo_tasks.reset_demo_clinics({}, now)


async def test_job_resets_demo_clinics_once_at_six(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _enabled(admin_client, client, ctx)
    # Вторая демо-клиника не готова (нет врачей) — первой она не мешает.
    broken = await _other_clinic(admin_client, client, "Демо пустая")
    assert (await _set_demo(admin_client, broken, name="Демо пустая")).status_code == 200
    # Обычная клиника заданию не видна вовсе.
    plain = await _other_clinic(admin_client, client, "Обычная клиника")

    six = (datetime.now(UTC).astimezone(_BISHKEK) + timedelta(days=1)).replace(
        hour=6, minute=0, second=0, microsecond=0
    )
    assert await _tick(monkeypatch, _resources, six - timedelta(minutes=10)) == (
        "собрано: 0, не удалось: 0"
    )
    assert await _tick(monkeypatch, _resources, six) == "собрано: 1, не удалось: 1"
    visits = await _visits(_resources, ctx)
    assert len(visits) == 7 and await _visits(_resources, plain) == []
    # Записи «только что»: пуш «не подтверждён» в шесть утра сотрудникам не уйдёт.
    async with _resources.factory() as session:
        assert await UnconfirmedVisitService(session).run(six.astimezone(UTC)) == 0

    # Следующий тик того же часа собранный день не пересобирает.
    again = await _tick(monkeypatch, _resources, six + timedelta(minutes=10))
    assert again == "собрано: 0, не удалось: 1"
    assert {v.id for v in visits} == {v.id for v in await _visits(_resources, ctx)}
    async with _resources.factory() as session:
        state = await session.get(ClinicDemo, uuid.UUID(ctx.clinic_id))
        assert state is not None and state.reset_at == six


async def test_scheduled_reminders_skip_demo_but_manual_one_works(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _template(_resources)
    await _enable(admin_client, client, ctx)
    await _enabled(admin_client, client, ctx)
    appointment_id = await _book(client, ctx, phone="0700900500", first_name="Гость")
    fake = _FakeWa()
    assert "напоминаний: 0" in await _run(monkeypatch, _resources, fake)
    async with _resources.factory() as session:
        service = PatientReminderService(session, fake)  # type: ignore[arg-type]
        assert await service._clinics_with_skill("debts") == []
    assert fake.templates == []

    async with _resources.factory() as session:
        visit = await session.get(Appointment, uuid.UUID(appointment_id))
        assert visit is not None
        visit.scheduled_at = datetime.now(UTC) + timedelta(hours=24)
        await session.commit()
    async with _resources.factory() as session:
        reminder = await VisitReminderService(session, fake).remind_now(  # type: ignore[arg-type]
            uuid.UUID(ctx.clinic_id), uuid.UUID(appointment_id)
        )
    assert reminder.status == "sent" and len(fake.templates) == 1

    # Со снятым признаком плановая рассылка снова идёт (новая запись).
    await _set_demo(admin_client, ctx, demo=False, name=None)
    await _book(client, ctx, phone="0700900501", first_name="Обычный", hour=11)
    assert "напоминаний: 1" in await _run(monkeypatch, _resources, fake)
    async with _resources.factory() as session:
        clinic = await session.get(Clinic, uuid.UUID(ctx.clinic_id))
        assert clinic is not None and clinic.demo is False
