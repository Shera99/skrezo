"""«Напомнить сейчас»: ``POST /clinic/appointments/{id}/reminder``.

Напоминание по кнопке сотрудника, не дожидаясь расписания и независимо от
ступени ассистента: приём на сегодня или завтра, в открытое окно — обычным
сообщением с кнопками, в закрытое — шаблоном, не чаще раза в 30 минут. Исход —
в том же поле ``reminder``, отказ WhatsApp запрос не роняет.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.config import settings
from app.core.errors import ConflictError, TooManyRequestsError, ValidationAppError
from app.main import app
from app.modules.ai.application.visits import VisitReminderService
from app.modules.whatsapp.presentation.deps import get_whatsapp_client
from app.shared.db.enums import AppointmentStatus, Lang
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.appointment_reminder import AppointmentReminder
from app.shared.db.models.audit_log import AuditLog
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.clinic_whatsapp import ClinicWhatsApp
from app.shared.db.models.patient import Patient
from app.shared.db.models.whatsapp_template import WhatsAppTemplate
from tests.api.conftest import Ctx
from tests.api.test_visit_reminders_api import (
    LOCAL_PHONE,
    WA_PHONE,
    _age_reminders,
    _book,
    _enable,
    _FakeWa,
    _press,
    _run,
    _template,
)
from tests.conftest import API, Resources, otp_login

#: «Сейчас» сервисных тестов: среда, 10:00 по Бишкеку — границы суток далеко.
_NOW = datetime(2027, 3, 10, 4, 0, tzinfo=UTC)


@pytest.fixture
def wa() -> Iterator[_FakeWa]:
    """WhatsApp, который запоминает отправленное, — вместо Meta в ручке."""
    fake = _FakeWa()
    app.dependency_overrides[get_whatsapp_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_whatsapp_client, None)


async def _place(
    res: Resources,
    appointment_id: str,
    when: datetime,
    status: AppointmentStatus = AppointmentStatus.SCHEDULED,
) -> None:
    """Поставить приём на точное время мимо рабочих часов — тесту нужна дата."""
    async with res.factory() as session:
        appointment = await session.get(Appointment, uuid.UUID(appointment_id))
        assert appointment is not None
        appointment.scheduled_at = when
        appointment.status = status
        await session.commit()


async def _booked(client: AsyncClient, ctx: Ctx, res: Resources, when: datetime) -> str:
    appointment_id = await _book(client, ctx, phone=LOCAL_PHONE, first_name="Айгуль")
    await _place(res, appointment_id, when)
    return appointment_id


async def _open_window(res: Resources, ctx: Ctx, at: datetime, lang: Lang = Lang.RU) -> None:
    """Пациент писал клинике — окно 24 часов открыто."""
    async with res.factory() as session:
        session.add(
            AiConversation(
                clinic_id=uuid.UUID(ctx.clinic_id),
                wa_phone=WA_PHONE,
                lang=lang,
                last_msg_at=at,
                last_patient_msg_at=at,
            )
        )
        await session.commit()


async def _set_clinic(res: Resources, ctx: Ctx, **fields: Any) -> None:
    async with res.factory() as session:
        clinic = await session.get(Clinic, uuid.UUID(ctx.clinic_id))
        assert clinic is not None
        for name, value in fields.items():
            setattr(clinic, name, value)
        await session.commit()


async def _remind(client: AsyncClient, ctx: Ctx, appointment_id: str) -> Any:
    return await client.post(
        f"{API}/clinic/appointments/{appointment_id}/reminder", headers=ctx.owner_headers
    )


async def _service_remind(
    res: Resources, ctx: Ctx, fake: _FakeWa, appointment_id: str, now: datetime = _NOW
) -> AppointmentReminder:
    async with res.factory() as session:
        return await VisitReminderService(session, fake).remind_now(  # type: ignore[arg-type]
            uuid.UUID(ctx.clinic_id), uuid.UUID(appointment_id), now
        )


async def test_closed_window_sends_template_and_repeats_only_after_half_an_hour(
    client: AsyncClient, ctx: Ctx, _resources: Resources, wa: _FakeWa
) -> None:
    """Ассистент выключен, умение не включали — кнопка работает всё равно."""
    await _template(_resources)
    appointment_id = await _booked(client, ctx, _resources, datetime.now(UTC) + timedelta(hours=24))

    sent = await _remind(client, ctx, appointment_id)
    assert sent.status_code == 200, sent.text
    assert sent.json()["id"] == appointment_id
    assert sent.json()["reminder"]["status"] == "sent"
    assert wa.buttons == [] and len(wa.templates) == 1
    assert wa.templates[0]["to"] == WA_PHONE
    assert wa.templates[0]["name"] == settings.wa_visit_reminder_template
    assert wa.templates[0]["buttons"] == [
        f"visit:{appointment_id}:confirm",
        f"visit:{appointment_id}:change",
    ]

    again = await _remind(client, ctx, appointment_id)
    assert again.status_code == 429, again.text
    assert again.json()["error"] == "reminder_too_soon"
    assert 0 < again.json()["retryAfter"] <= 30 * 60
    assert again.headers["Retry-After"] == str(again.json()["retryAfter"])
    assert len(wa.templates) == 1

    await _age_reminders(_resources, minutes=31)
    third = await _remind(client, ctx, appointment_id)
    assert third.status_code == 200, third.text
    assert len(wa.templates) == 2
    async with _resources.factory() as session:
        # Строка у приёма одна: повтор обновляет её, а не копит.
        reminder = (
            await session.execute(
                select(AppointmentReminder).where(
                    AppointmentReminder.appointment_id == uuid.UUID(appointment_id)
                )
            )
        ).scalar_one()
        assert reminder.wa_message_id == "fake-tpl-2"
        audit = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == uuid.UUID(appointment_id),
                        AuditLog.action == "appointment.remind",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert [row.payload for row in audit] == [{"status": "sent"}] * 2
    assert all(str(row.clinic_id) == ctx.clinic_id for row in audit)


async def test_open_window_sends_plain_message_with_the_same_buttons(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources, wa: _FakeWa
) -> None:
    await _template(_resources)
    now = datetime.now(UTC)
    appointment_id = await _booked(client, ctx, _resources, now + timedelta(hours=24))
    await _open_window(_resources, ctx, now - timedelta(hours=1))

    sent = await _remind(client, ctx, appointment_id)
    assert sent.status_code == 200, sent.text
    assert wa.templates == [] and len(wa.buttons) == 1
    message = wa.buttons[0]
    assert message["to"] == WA_PHONE
    # Текст — тот же, что у одобренного шаблона «накануне».
    assert message["body"].startswith("Айгуль, напоминаем о приёме")
    assert message["buttons"] == [
        (f"visit:{appointment_id}:confirm", "Приду"),
        (f"visit:{appointment_id}:change", "Перенести"),
    ]

    # Кнопка обычного сообщения работает так же, как кнопка шаблона.
    await _press(admin_client, ctx, f"visit:{appointment_id}:confirm")
    detail = await client.get(
        f"{API}/clinic/appointments/{appointment_id}", headers=ctx.owner_headers
    )
    assert detail.json()["status"] == "confirmed"
    assert detail.json()["reminder"]["status"] == "confirmed"


async def test_open_window_without_template_uses_own_text_for_each_day(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    fake = _FakeWa()
    appointment_id = await _booked(client, ctx, _resources, _NOW + timedelta(hours=29))
    await _open_window(_resources, ctx, _NOW - timedelta(hours=2))
    await _set_clinic(_resources, ctx, address=None)

    await _service_remind(_resources, ctx, fake, appointment_id)  # завтра, 15:00
    assert "завтра в 15:00" in fake.buttons[-1]["body"]
    assert "Клиника ИИ" in fake.buttons[-1]["body"]

    await _place(_resources, appointment_id, _NOW + timedelta(hours=5))  # сегодня, 15:00
    later = _NOW + timedelta(minutes=31)
    await _service_remind(_resources, ctx, fake, appointment_id, later)
    assert "сегодня в 15:00" in fake.buttons[-1]["body"]
    assert "Адрес" not in fake.buttons[-1]["body"]

    await _set_clinic(_resources, ctx, address="Бишкек, Чуй 1")
    await _service_remind(_resources, ctx, fake, appointment_id, later + timedelta(minutes=31))
    assert "Адрес: Бишкек, Чуй 1." in fake.buttons[-1]["body"]
    assert fake.templates == []


async def test_open_window_speaks_kyrgyz_with_the_patient(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Шаблон только русский, диалог кыргызский — свой текст, а не чужой язык."""
    await _template(_resources)
    fake = _FakeWa()
    appointment_id = await _booked(client, ctx, _resources, _NOW + timedelta(hours=29))
    await _open_window(_resources, ctx, _NOW - timedelta(hours=2), Lang.KG)

    await _service_remind(_resources, ctx, fake, appointment_id)
    assert "эртең саат 15:00" in fake.buttons[0]["body"]
    assert [title for _, title in fake.buttons[0]["buttons"]] == ["Келем", "Которуу"]


async def test_closed_window_today_prefers_the_day_template_when_address_is_known(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    await _template(_resources)
    async with _resources.factory() as session:
        session.add(
            WhatsAppTemplate(
                clinic_id=None,
                meta_name=settings.wa_visit_reminder_today_template,
                lang="ru",
                category="UTILITY",
                body_preview="Ждём вас сегодня в {{1}} в клинике {{2}}. Адрес: {{3}}.",
                variables=["1", "2", "3"],
                status="approved",
            )
        )
        await session.commit()
    fake = _FakeWa()
    appointment_id = await _booked(client, ctx, _resources, _NOW + timedelta(hours=5))

    # Без адреса пустую переменную Meta не примет — уходит общий шаблон.
    await _set_clinic(_resources, ctx, address=None)
    await _service_remind(_resources, ctx, fake, appointment_id)
    assert fake.templates[-1]["name"] == settings.wa_visit_reminder_template
    assert fake.templates[-1]["vars"] == ["Айгуль", "10.03", "15:00"]

    await _set_clinic(_resources, ctx, address="Бишкек, Чуй 1")
    await _service_remind(_resources, ctx, fake, appointment_id, _NOW + timedelta(minutes=31))
    assert fake.templates[-1]["name"] == settings.wa_visit_reminder_today_template
    assert fake.templates[-1]["vars"] == ["15:00", "Клиника ИИ", "Бишкек, Чуй 1"]


@pytest.mark.parametrize(
    ("shift", "status", "reason"),
    [
        (timedelta(hours=5), AppointmentStatus.CONFIRMED, "status"),
        (timedelta(hours=-1), AppointmentStatus.SCHEDULED, "started"),
        (timedelta(hours=38), AppointmentStatus.SCHEDULED, "day"),  # послезавтра, 00:00
    ],
)
async def test_only_unconfirmed_visits_of_today_and_tomorrow(
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    shift: timedelta,
    status: AppointmentStatus,
    reason: str,
) -> None:
    await _template(_resources)
    fake = _FakeWa()
    appointment_id = await _booked(client, ctx, _resources, _NOW + timedelta(hours=1))
    await _place(_resources, appointment_id, _NOW + shift, status)

    with pytest.raises(ValidationAppError) as refused:
        await _service_remind(_resources, ctx, fake, appointment_id)
    assert refused.value.code == "reminder_not_available"
    assert refused.value.extra == {"reason": reason}
    assert fake.templates == [] and fake.buttons == []


async def test_refused_send_is_failed_and_can_be_repeated_at_once(
    client: AsyncClient, ctx: Ctx, _resources: Resources, wa: _FakeWa
) -> None:
    await _template(_resources)
    appointment_id = await _booked(client, ctx, _resources, datetime.now(UTC) + timedelta(hours=24))
    wa.refuse_code = 131026
    refused = await _remind(client, ctx, appointment_id)
    assert refused.status_code == 200, refused.text
    assert refused.json()["reminder"]["status"] == "failed"
    assert "WhatsApp не доставил сообщение" in refused.json()["reminder"]["error"]

    # Пациент ничего не получил — причину исправили, повтор нужен сразу.
    wa.refuse_code = None
    repeated = await _remind(client, ctx, appointment_id)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["reminder"]["status"] == "sent"
    assert repeated.json()["reminder"]["error"] is None


async def test_closed_window_without_template_is_failed_with_reason(
    client: AsyncClient, ctx: Ctx, _resources: Resources, wa: _FakeWa
) -> None:
    appointment_id = await _booked(client, ctx, _resources, datetime.now(UTC) + timedelta(hours=24))
    resp = await _remind(client, ctx, appointment_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["reminder"]["status"] == "failed"
    assert "нет одобренного шаблона" in resp.json()["reminder"]["error"]


async def test_access_whatsapp_and_foreign_clinic(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, _resources: Resources, wa: _FakeWa
) -> None:
    appointment_id = await _booked(client, ctx, _resources, datetime.now(UTC) + timedelta(hours=24))
    anonymous = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/reminder",
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert anonymous.status_code == 401
    unknown = await _remind(client, ctx, str(uuid.uuid4()))
    assert unknown.status_code == 404 and unknown.json()["error"] == "appointment_not_found"

    # Врач по умолчанию пациентам не пишет — права reminders.manage у роли нет.
    spec = (await client.get(f"{API}/specializations")).json()[0]["id"]
    added = await client.post(
        f"{API}/clinic/staff",
        headers=ctx.owner_headers,
        json={"fullName": "Врач Без Права", "phone": "0700977001", "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    worker = await otp_login(client, "0700977001")
    denied = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/reminder", headers=worker
    )
    assert denied.status_code == 403, denied.text

    # Приём чужой клиники не виден даже её владельцу по id.
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    other = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Соседний",
            "adminPhone": "0700977002",
            "name": "Соседняя клиника",
            "cityId": city,
            "businessPhone": "0700000001",
            "seats": 2,
        },
    )
    assert other.status_code == 201, other.text
    stranger = await otp_login(client, "0700977002")
    foreign = await client.post(
        f"{API}/clinic/appointments/{appointment_id}/reminder", headers=stranger
    )
    assert foreign.status_code == 404

    async with _resources.factory() as session:
        binding = (
            await session.execute(
                select(ClinicWhatsApp).where(ClinicWhatsApp.clinic_id == uuid.UUID(ctx.clinic_id))
            )
        ).scalar_one()
        binding.active = False
        await session.commit()
    disconnected = await _remind(client, ctx, appointment_id)
    assert disconnected.status_code == 409
    assert disconnected.json()["error"] == "whatsapp_not_connected"
    assert wa.templates == [] and wa.buttons == []


async def test_simultaneous_presses_send_one_message(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Пока одно нажатие отправляется, второе получает 429, а не второе сообщение."""
    await _template(_resources)
    fake = _FakeWa()
    appointment_id = await _booked(client, ctx, _resources, _NOW + timedelta(hours=5))
    async with _resources.factory() as holder:
        key = func.hashtextextended(f"visit-reminder:{appointment_id}", 0)
        await holder.execute(select(func.pg_advisory_xact_lock(key)))
        with pytest.raises(TooManyRequestsError) as busy:
            await _service_remind(_resources, ctx, fake, appointment_id)
        assert busy.value.code == "reminder_too_soon"
    assert fake.templates == []
    await _service_remind(_resources, ctx, fake, appointment_id)  # замок отпущен
    assert len(fake.templates) == 1


async def test_scheduled_run_does_not_repeat_manual_reminder(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    wa: _FakeWa,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _template(_resources)
    await _enable(admin_client, client, ctx)
    appointment_id = await _booked(client, ctx, _resources, datetime.now(UTC) + timedelta(hours=24))
    assert (await _remind(client, ctx, appointment_id)).status_code == 200
    planned = _FakeWa()
    assert "напоминаний: 0" in await _run(monkeypatch, _resources, planned)
    assert planned.templates == []


async def test_manual_reminder_shortly_before_visit_gets_time_to_answer(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Контрольный срок уже прошёл, но «не ответил» — не раньше чем через полчаса."""
    await _template(_resources)
    appointment_id = await _booked(client, ctx, _resources, _NOW + timedelta(hours=2))
    await _service_remind(_resources, ctx, _FakeWa(), appointment_id)

    async with _resources.factory() as session:
        service = VisitReminderService(session, _FakeWa())  # type: ignore[arg-type]
        assert await service.close_unanswered(_NOW + timedelta(minutes=10)) == 0
        assert await service.close_unanswered(_NOW + timedelta(minutes=30)) == 1
    async with _resources.factory() as session:
        status = (
            await session.execute(
                select(AppointmentReminder.status).where(
                    AppointmentReminder.appointment_id == uuid.UUID(appointment_id)
                )
            )
        ).scalar_one()
    assert status == "no_answer"


async def test_service_refuses_clinic_without_whatsapp(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    appointment_id = await _booked(client, ctx, _resources, _NOW + timedelta(hours=5))
    async with _resources.factory() as session:
        await session.execute(
            ClinicWhatsApp.__table__.delete().where(  # type: ignore[attr-defined]
                ClinicWhatsApp.clinic_id == uuid.UUID(ctx.clinic_id)
            )
        )
        await session.commit()
    with pytest.raises(ConflictError):
        await _service_remind(_resources, ctx, _FakeWa(), appointment_id)
    async with _resources.factory() as session:
        # Отказ до отправки следов у приёма не оставляет.
        reminders = (await session.execute(select(func.count(AppointmentReminder.id)))).scalar_one()
        patients = (await session.execute(select(func.count(Patient.id)))).scalar_one()
    assert reminders == 0 and patients == 1
