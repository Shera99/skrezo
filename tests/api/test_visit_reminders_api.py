"""Ф7, инкремент 4: подтверждение визита из напоминания.

За сутки до приёма — шаблон WhatsApp с кнопками; «Приду» переводит приём в
confirmed мимо модели штатным use-case; «Перенести» возвращает в диалог;
без ответа к контрольному сроку — событие администратору. Напоминание одно
на приём и только при включённом умении.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.jobs.tasks.visits as visit_tasks
from app.core.config import settings
from app.shared.db.enums import AppointmentStatus
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.appointment_reminder import AppointmentReminder
from app.shared.db.models.audit_log import AuditLog
from app.shared.db.models.event import Event
from app.shared.db.models.whatsapp_template import WhatsAppTemplate
from app.shared.whatsapp_cloud import SentMessage
from tests.api.conftest import Ctx, _set_mode
from tests.conftest import API, Resources, own_membership_id

WA_PHONE = "+996700900500"
LOCAL_PHONE = "0700900500"


class _FakeWa:
    """Записывает отправленные шаблоны и тексты; без Meta."""

    def __init__(self) -> None:
        self.templates: list[dict[str, Any]] = []
        self.texts: list[tuple[str, str]] = []

    async def send_text(self, phone_number_id: str, to_phone: str, body: str) -> SentMessage:
        self.texts.append((to_phone, body))
        return SentMessage(wa_message_id=f"fake-text-{len(self.texts)}")

    async def send_template(
        self,
        phone_number_id: str,
        to_phone: str,
        template_name: str,
        lang_code: str,
        variables: list[str],
        buttons: list[str] | None = None,
    ) -> SentMessage:
        self.templates.append(
            {
                "to": to_phone,
                "name": template_name,
                "lang": lang_code,
                "vars": variables,
                "buttons": buttons,
            }
        )
        return SentMessage(wa_message_id=f"fake-tpl-{len(self.templates)}")

    async def fetch_media(self, media_id: str) -> Any:
        return None

    async def send_confirm_button(self, *args: Any, **kwargs: Any) -> SentMessage:
        return SentMessage(wa_message_id="fake-btn")

    async def check_token(self, phone_number_id: str) -> bool:
        return True

    async def fetch_templates(self, waba_id: str) -> Any:
        return None


#: Горизонт напоминаний в тестах: больше самого далёкого слота из _tomorrow()
#: (суббота 06:00 по Бишкеку → понедельник 10:00 = 52 часа).
_HORIZON_HOURS = 72


def _tomorrow(hour: int = 10) -> str:
    """Ближайший рабочий день после сегодняшнего (воскресенье — выходной), ``hour`` по Бишкеку.

    До такого слота бывает и больше суток: из субботы это понедельник (до 52
    часов), а с 06:00 до 10:00 по Бишкеку «завтра в 10:00» — дальше 24 часов в
    любой день. Поэтому горизонт напоминаний в этих тестах шире суток
    (:data:`_HORIZON_HOURS`), иначе они зависели бы от дня и часа запуска.
    """
    day = datetime.now(UTC).date() + timedelta(days=1)
    if day.weekday() == 6:
        day += timedelta(days=1)
    return f"{day.isoformat()}T{hour:02d}:00:00+06:00"


async def _book(
    client: AsyncClient, ctx: Ctx, *, phone: str, first_name: str, hour: int = 10
) -> str:
    patient = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.owner_headers,
        json={"phone": phone, "firstName": first_name, "lastName": "Тестова"},
    )
    assert patient.status_code == 201, patient.text
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    appointment = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.owner_headers,
        json={
            "patientId": patient.json()["id"],
            "doctorMembershipId": doctor_id,
            "scheduledAt": _tomorrow(hour),
        },
    )
    assert appointment.status_code == 201, appointment.text
    return str(appointment.json()["id"])


async def _template(res: Resources, *, lang: str = "ru") -> None:
    """Общий одобренный шаблон платформы: {{1}} имя, {{2}} дата, {{3}} время."""
    async with res.factory() as session:
        session.add(
            WhatsAppTemplate(
                clinic_id=None,
                meta_name=settings.wa_visit_reminder_template,
                lang=lang,
                category="UTILITY",
                body_preview="{{1}}, напоминаем о приёме {{2}} в {{3}}. Подтвердите, пожалуйста.",
                variables=["1", "2", "3"],
                status="approved",
            )
        )
        await session.commit()


async def _enable(admin_client: AsyncClient, client: AsyncClient, ctx: Ctx) -> None:
    await _set_mode(admin_client, ctx, "auto")
    resp = await client.put(
        f"{API}/clinic/assistant/trust",
        headers=ctx.owner_headers,
        json={"skills": {"reminders": "auto"}},
    )
    assert resp.status_code == 200, resp.text


async def _run(monkeypatch: pytest.MonkeyPatch, res: Resources, fake: _FakeWa) -> str:
    monkeypatch.setattr(visit_tasks, "session_factory", res.factory)
    monkeypatch.setattr(visit_tasks, "build_whatsapp_client", lambda: fake)
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory", res.factory
    )
    # Слот из _tomorrow() всегда попадает в горизонт, в какой бы день и час ни
    # шёл прогон: проверяется поток напоминания, а не длина горизонта.
    monkeypatch.setattr(settings, "visit_reminder_hours_before", _HORIZON_HOURS)
    return await visit_tasks.send_visit_reminders({})


async def _reminder(res: Resources, appointment_id: str) -> AppointmentReminder | None:
    async with res.factory() as session:
        return (
            await session.execute(
                select(AppointmentReminder).where(
                    AppointmentReminder.appointment_id == uuid.UUID(appointment_id)
                )
            )
        ).scalar_one_or_none()


async def _press(admin_client: AsyncClient, ctx: Ctx, payload: str) -> Any:
    resp = await admin_client.post(
        f"{API}/admin/ai/simulate-inbound",
        json={
            "clinicId": ctx.clinic_id,
            "fromPhone": WA_PHONE,
            "text": "-",
            "buttonPayload": payload,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_reminder_goes_once_with_buttons_and_only_when_enabled(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _template(_resources)
    appointment_id = await _book(client, ctx, phone=LOCAL_PHONE, first_name="Айгуль")
    fake = _FakeWa()

    # Умение выключено по умолчанию — ничего не уходит.
    await _set_mode(admin_client, ctx, "auto")
    assert "напоминаний: 0" in await _run(monkeypatch, _resources, fake)
    assert fake.templates == [] and await _reminder(_resources, appointment_id) is None

    await _enable(admin_client, client, ctx)
    assert "напоминаний: 1" in await _run(monkeypatch, _resources, fake)
    sent = fake.templates[0]
    assert sent["to"] == WA_PHONE and sent["name"] == settings.wa_visit_reminder_template
    assert sent["vars"][0] == "Айгуль" and sent["vars"][2] == "10:00"
    assert sent["buttons"] == [f"visit:{appointment_id}:confirm", f"visit:{appointment_id}:change"]
    reminder = await _reminder(_resources, appointment_id)
    assert (
        reminder is not None
        and reminder.status == "sent"
        and reminder.wa_message_id == "fake-tpl-1"
    )
    async with _resources.factory() as session:
        message = (
            await session.execute(
                select(AiMessage).where(AiMessage.conversation_id == reminder.conversation_id)
            )
        ).scalar_one()
    assert "Айгуль, напоминаем о приёме" in message.body and message.sent_at is not None

    # Повтор задания — второго напоминания нет.
    assert "напоминаний: 0" in await _run(monkeypatch, _resources, fake)
    assert len(fake.templates) == 1


async def test_confirm_button_sets_confirmed_and_change_button_returns_to_dialog(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _template(_resources)
    await _enable(admin_client, client, ctx)
    appointment_id = await _book(client, ctx, phone=LOCAL_PHONE, first_name="Айгуль")
    await _run(monkeypatch, _resources, _FakeWa())

    pressed = await _press(admin_client, ctx, f"visit:{appointment_id}:confirm")
    assert pressed["outcome"] == "button"
    assert any("Ждём вас" in (m["body"] or "") for m in pressed["messages"])
    async with _resources.factory() as session:
        appointment = await session.get(Appointment, uuid.UUID(appointment_id))
        assert appointment is not None and appointment.status is AppointmentStatus.CONFIRMED
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == uuid.UUID(appointment_id),
                    AuditLog.action == "appointment.update",
                )
            )
        ).scalar_one()
        assert audit.actor_id is None and audit.payload == {
            "fields": ["status"],
            "via": "assistant",
        }
    reminder = await _reminder(_resources, appointment_id)
    assert (
        reminder is not None and reminder.status == "confirmed" and reminder.answered_at is not None
    )

    again = await _press(admin_client, ctx, f"visit:{appointment_id}:confirm")
    assert any("уже подтверждён" in (m["body"] or "") for m in again["messages"])

    # «Перенести» — вопрос уходит в диалог с ботом, приём не трогаем.
    other_id = await _book(client, ctx, phone="0700900501", first_name="Бермет", hour=11)
    foreign = await _press(admin_client, ctx, f"visit:{other_id}:change")  # чужая запись
    assert any("Не нашёл эту запись" in (m["body"] or "") for m in foreign["messages"])
    change = await _press(admin_client, ctx, f"visit:{appointment_id}:change")
    assert any("на какой день и время перенести" in (m["body"] or "") for m in change["messages"])
    reminder = await _reminder(_resources, appointment_id)
    assert reminder is not None and reminder.status == "change_requested"


async def test_unanswered_reminder_escalates_to_admin(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _template(_resources)
    await _enable(admin_client, client, ctx)
    appointment_id = await _book(client, ctx, phone=LOCAL_PHONE, first_name="Айгуль")
    fake = _FakeWa()
    assert "напоминаний: 1" in await _run(monkeypatch, _resources, fake)

    # Контрольный срок сдвинут дальше самого далёкого слота из _tomorrow() — визит
    # уже «без ответа» при любом дне запуска (48 часов не хватало по субботам).
    monkeypatch.setattr(settings, "visit_unconfirmed_hours_before", _HORIZON_HOURS)
    summary = await _run(monkeypatch, _resources, fake)
    assert "без ответа: 1" in summary
    reminder = await _reminder(_resources, appointment_id)
    assert reminder is not None and reminder.status == "no_answer"
    async with _resources.factory() as session:
        event = (
            await session.execute(
                select(Event).where(
                    Event.type == "appointment.unconfirmed",
                    Event.entity_id == uuid.UUID(appointment_id),
                )
            )
        ).scalar_one()
    assert event.clinic_id == uuid.UUID(ctx.clinic_id)
    # Второй прогон не дублирует событие.
    assert "без ответа: 0" in await _run(monkeypatch, _resources, fake)
