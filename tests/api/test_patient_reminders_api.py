"""Ф7, инкремент 5: напоминания о долге и повторном осмотре.

Долг: завершённый приём с остатком по кассе старше N дней — один шаблон на
приём, свежий или оплаченный долг не трогаем. Повторный осмотр: последний
завершённый визит старше N месяцев и нет будущей записи — шаблон с кнопками;
«Записаться» возвращает в диалог, «Не сейчас» — снимает вопрос. Оба — только
при включённом умении, оба идемпотентны.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.jobs.tasks.patient_reminders as reminder_tasks
from app.core.config import settings
from app.shared.db.enums import AppointmentStatus, AppointmentType, PaymentKind, PaymentMethod
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.cash_payment import CashPayment
from app.shared.db.models.patient_reminder import PatientReminder
from app.shared.db.models.whatsapp_template import WhatsAppTemplate
from app.shared.whatsapp_cloud import SentMessage
from tests.api.conftest import Ctx, _set_mode
from tests.conftest import API, Resources, own_membership_id


class _FakeWa:
    def __init__(self) -> None:
        self.templates: list[dict[str, Any]] = []

    async def send_text(self, phone_number_id: str, to_phone: str, body: str) -> SentMessage:
        return SentMessage(wa_message_id="fake-text")

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
            {"to": to_phone, "name": template_name, "vars": variables, "buttons": buttons}
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


async def _patient(client: AsyncClient, ctx: Ctx, phone: str, first_name: str) -> str:
    resp = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.owner_headers,
        json={"phone": phone, "firstName": first_name, "lastName": "Тестова"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


_slot_seq = 0


async def _completed(
    res: Resources,
    ctx: Ctx,
    patient_id: str,
    doctor_id: str,
    *,
    days_ago: int,
    price: int = 250_000,
) -> str:
    """Завершённый приём в прошлом (прямо в БД: путь статусов здесь не предмет теста).

    Каждый — в своём часу: у врача не бывает двух приёмов разом (EXCLUDE).
    """
    global _slot_seq
    _slot_seq += 1
    async with res.factory() as session:
        appointment = Appointment(
            clinic_id=uuid.UUID(ctx.clinic_id),
            patient_id=uuid.UUID(patient_id),
            doctor_membership_id=uuid.UUID(doctor_id),
            appointment_type=AppointmentType.TREATMENT,
            scheduled_at=datetime.now(UTC) - timedelta(days=days_ago, hours=_slot_seq),
            duration_min=30,
            status=AppointmentStatus.COMPLETED,
            price_total_tiyin=price,
        )
        session.add(appointment)
        await session.commit()
        return str(appointment.id)


async def _pay(res: Resources, ctx: Ctx, patient_id: str, appointment_id: str, amount: int) -> None:
    async with res.factory() as session:
        session.add(
            CashPayment(
                clinic_id=uuid.UUID(ctx.clinic_id),
                appointment_id=uuid.UUID(appointment_id),
                patient_id=uuid.UUID(patient_id),
                amount_tiyin=amount,
                method=PaymentMethod.CASH,
                kind=PaymentKind.PAYMENT,
            )
        )
        await session.commit()


async def _templates(res: Resources) -> None:
    async with res.factory() as session:
        session.add_all(
            [
                WhatsAppTemplate(
                    clinic_id=None,
                    meta_name=settings.wa_debt_reminder_template,
                    lang="ru",
                    category="UTILITY",
                    body_preview="{{1}}, по приёму остался долг {{2}} сом. Клиника {{3}}.",
                    variables=["1", "2", "3"],
                    status="approved",
                ),
                WhatsAppTemplate(
                    clinic_id=None,
                    meta_name=settings.wa_recall_reminder_template,
                    lang="ru",
                    category="MARKETING",
                    body_preview="{{1}}, прошло {{2}} мес. с визита в {{3}} — пора на осмотр.",
                    variables=["1", "2", "3"],
                    status="approved",
                ),
            ]
        )
        await session.commit()


async def _skills(client: AsyncClient, ctx: Ctx, **caps: str) -> None:
    resp = await client.put(
        f"{API}/clinic/assistant/trust", headers=ctx.owner_headers, json={"skills": caps}
    )
    assert resp.status_code == 200, resp.text


async def _run(monkeypatch: pytest.MonkeyPatch, res: Resources, fake: _FakeWa) -> str:
    monkeypatch.setattr(reminder_tasks, "session_factory", res.factory)
    monkeypatch.setattr(reminder_tasks, "build_whatsapp_client", lambda: fake)
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory", res.factory
    )
    return await reminder_tasks.send_patient_reminders({})


async def _reminders(res: Resources, kind: str) -> list[PatientReminder]:
    async with res.factory() as session:
        return list(
            (await session.execute(select(PatientReminder).where(PatientReminder.kind == kind)))
            .scalars()
            .all()
        )


async def _press(admin_client: AsyncClient, ctx: Ctx, phone: str, payload: str) -> Any:
    resp = await admin_client.post(
        f"{API}/admin/ai/simulate-inbound",
        json={"clinicId": ctx.clinic_id, "fromPhone": phone, "text": "-", "buttonPayload": payload},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_debt_reminder_goes_once_for_old_unpaid_debts_only(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _templates(_resources)
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    aigul = await _patient(client, ctx, "0700910500", "Айгуль")
    bermet = await _patient(client, ctx, "0700910501", "Бермет")
    old_unpaid = await _completed(_resources, ctx, aigul, doctor_id, days_ago=5)
    old_paid = await _completed(_resources, ctx, bermet, doctor_id, days_ago=5)
    await _pay(_resources, ctx, bermet, old_paid, 250_000)
    await _completed(_resources, ctx, aigul, doctor_id, days_ago=1)  # свежий — рано
    fake = _FakeWa()

    assert "о долге: 0" in await _run(monkeypatch, _resources, fake)  # умение выключено
    await _skills(client, ctx, debts="auto")
    assert "о долге: 1" in await _run(monkeypatch, _resources, fake)
    sent = fake.templates[0]
    assert sent["to"] == "+996700910500" and sent["name"] == settings.wa_debt_reminder_template
    assert sent["vars"] == ["Айгуль", "2 500", "Демо-клиника"] or sent["vars"][:2] == [
        "Айгуль",
        "2 500",
    ]
    assert sent["buttons"] is None
    reminders = await _reminders(_resources, "debt")
    assert [str(r.appointment_id) for r in reminders] == [old_unpaid]
    assert reminders[0].status == "sent" and reminders[0].wa_message_id == "fake-tpl-1"
    async with _resources.factory() as session:
        copy = (
            await session.execute(
                select(AiMessage).where(AiMessage.conversation_id == reminders[0].conversation_id)
            )
        ).scalar_one()
    assert "остался долг 2 500 сом" in copy.body

    assert "о долге: 0" in await _run(monkeypatch, _resources, fake)  # повтор — без дублей
    assert len(fake.templates) == 1


async def test_recall_invites_after_months_without_upcoming_and_buttons_answer(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _templates(_resources)
    await _set_mode(admin_client, ctx, "auto")
    await _skills(client, ctx, recall="auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    aigul = await _patient(client, ctx, "0700910510", "Айгуль")
    bermet = await _patient(client, ctx, "0700910511", "Бермет")
    nurlan = await _patient(client, ctx, "0700910512", "Нурлан")
    last_visit = await _completed(_resources, ctx, aigul, doctor_id, days_ago=7 * 30, price=0)
    await _completed(
        _resources, ctx, aigul, doctor_id, days_ago=12 * 30, price=0
    )  # старее последнего
    bermet_visit = await _completed(_resources, ctx, bermet, doctor_id, days_ago=8 * 30, price=0)
    await _completed(_resources, ctx, nurlan, doctor_id, days_ago=2 * 30, price=0)  # рано
    # У Бермет есть будущая запись — приглашать незачем.
    day = datetime.now(UTC).date() + timedelta(days=2)
    if day.weekday() == 6:
        day += timedelta(days=1)
    upcoming = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.owner_headers,
        json={
            "patientId": bermet,
            "doctorMembershipId": doctor_id,
            "scheduledAt": f"{day.isoformat()}T14:00:00+06:00",
        },
    )
    assert upcoming.status_code == 201, upcoming.text
    fake = _FakeWa()

    assert "о повторном осмотре: 1" in await _run(monkeypatch, _resources, fake)
    sent = fake.templates[0]
    assert sent["to"] == "+996700910510" and sent["name"] == settings.wa_recall_reminder_template
    assert sent["vars"][:2] == ["Айгуль", str(settings.recall_months)]
    assert sent["buttons"] == [f"recall:{last_visit}:book", f"recall:{last_visit}:later"]
    reminders = await _reminders(_resources, "recall")
    assert [str(r.appointment_id) for r in reminders] == [last_visit]
    assert bermet_visit not in [str(r.appointment_id) for r in reminders]

    later = await _press(admin_client, ctx, "+996700910510", f"recall:{last_visit}:later")
    assert any("напомним позже" in (m["body"] or "") for m in later["messages"])
    assert (await _reminders(_resources, "recall"))[0].status == "snoozed"
    book = await _press(admin_client, ctx, "+996700910510", f"recall:{last_visit}:book")
    assert any("подберу варианты" in (m["body"] or "") for m in book["messages"])
    assert (await _reminders(_resources, "recall"))[0].status == "answered"
    foreign = await _press(admin_client, ctx, "+996700910511", f"recall:{last_visit}:book")
    assert any("Не нашёл это приглашение" in (m["body"] or "") for m in foreign["messages"])

    assert "о повторном осмотре: 0" in await _run(monkeypatch, _resources, fake)  # без дублей
