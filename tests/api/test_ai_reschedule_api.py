"""Ф7, инкремент 2: перенос и отмена записи пациентом через ассистента.

Та же механика, что у записи: модель только предлагает (propose_reschedule /
propose_cancel → резерв + кнопка), действие выполняет обработчик кнопки
штатным use-case правки приёма. Только свои будущие записи; повтор кнопки
идемпотентен; в аудите — «ассистент по кнопке пациента», а не сотрудник.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.shared.ai import AiMessagePart, AiResult, ToolCallRequest
from app.shared.db.enums import AppointmentStatus, CancelReason
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.audit_log import AuditLog
from app.shared.db.models.slot_hold import SlotHold
from tests.api.conftest import Ctx, _FakeProvider, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources, own_membership_id

WA_PHONE = "+996700700500"
LOCAL_PHONE = "0700700500"


def _workday(hour: int, *, days_ahead: int = 1) -> str:
    """Ближайший рабочий день (воскресенье — выходной) в заданный час, +06:00."""
    day = datetime.now(UTC).date() + timedelta(days=days_ahead)
    if day.weekday() == 6:
        day += timedelta(days=1)
    return f"{day.isoformat()}T{hour:02d}:00:00+06:00"


def _tool_result(parts: list[AiMessagePart], tool: str) -> Any:
    part = next(p for p in parts if p.role == "tool" and p.tool_name == tool)
    return part.tool_result


async def _press(admin_client: AsyncClient, ctx: Ctx, phone: str, payload: str) -> Any:
    resp = await admin_client.post(
        f"{API}/admin/ai/simulate-inbound",
        json={"clinicId": ctx.clinic_id, "fromPhone": phone, "text": "-", "buttonPayload": payload},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _book(
    client: AsyncClient, ctx: Ctx, *, phone: str, first_name: str, slot: str
) -> tuple[str, str]:
    """Пациент с телефоном (по нему привяжется диалог) и его запись из кабинета."""
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
            "scheduledAt": slot,
        },
    )
    assert appointment.status_code == 201, appointment.text
    return str(appointment.json()["id"]), doctor_id


async def _appointment(res: Resources, appointment_id: str) -> Appointment:
    async with res.factory() as session:
        return (
            await session.execute(
                select(Appointment).where(Appointment.id == uuid.UUID(appointment_id))
            )
        ).scalar_one()


async def test_patient_reschedules_own_appointment_by_button(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    appointment_id, _ = await _book(
        client, ctx, phone=LOCAL_PHONE, first_name="Айгуль", slot=_workday(10)
    )
    new_slot = _workday(11)

    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="get_patient_appointments", args={})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_reschedule",
                args={"appointmentId": appointment_id, "startsAt": new_slot},
            )
        ),
        AiResult(
            text=_reply_json(
                "Отправил подтверждение переноса — нажмите кнопку.", intent="reschedule"
            )
        ),
    ]
    body = await _simulate(admin_client, ctx, "Перенесите мою запись на 11:00", WA_PHONE)
    assert body["outcome"] == "sent", body

    listed = _tool_result(fake.calls[1]["parts"], "get_patient_appointments")
    assert listed[0]["appointmentId"] == appointment_id and listed[0]["canChange"] is True
    proposed = _tool_result(fake.calls[2]["parts"], "propose_reschedule")
    assert proposed["kind"] == "reschedule" and proposed["buttonSent"] is True
    assert proposed["fromStartsAt"] and proposed["startsAt"].startswith(new_slot[:13])
    async with _resources.factory() as session:
        hold = (await session.execute(select(SlotHold))).scalar_one()
        assert hold.kind == "reschedule" and str(hold.target_appointment_id) == appointment_id
        assert hold.consumed_at is None
    assert (await _appointment(_resources, appointment_id)).scheduled_at == datetime.fromisoformat(
        _workday(10)
    )

    pressed = await _press(admin_client, ctx, WA_PHONE, proposed["confirmationId"])
    assert pressed["outcome"] == "button"
    assert any("перенесли" in (m["body"] or "") for m in pressed["messages"])
    moved = await _appointment(_resources, appointment_id)
    assert moved.scheduled_at == datetime.fromisoformat(new_slot)
    assert moved.status is AppointmentStatus.SCHEDULED
    async with _resources.factory() as session:
        hold = (await session.execute(select(SlotHold))).scalar_one()
        assert str(hold.appointment_id) == appointment_id and hold.consumed_at is not None
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == uuid.UUID(appointment_id),
                    AuditLog.action == "appointment.update",
                )
            )
        ).scalar_one()
        assert audit.actor_id is None and audit.payload == {
            "fields": ["doctor_membership_id", "scheduled_at"],
            "via": "assistant",
        }

    again = await _press(admin_client, ctx, WA_PHONE, proposed["confirmationId"])
    assert any("уже выполнен" in (m["body"] or "") for m in again["messages"])
    assert (await _appointment(_resources, appointment_id)).scheduled_at == datetime.fromisoformat(
        new_slot
    )
    assert len(fake.calls) == 3  # кнопку модель не видела


async def test_patient_cancels_own_appointment_by_button(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    appointment_id, _ = await _book(
        client, ctx, phone=LOCAL_PHONE, first_name="Айгуль", slot=_workday(10)
    )

    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="get_patient_appointments", args={})),
        AiResult(
            tool_call=ToolCallRequest(name="propose_cancel", args={"appointmentId": appointment_id})
        ),
        AiResult(
            text=_reply_json("Отправил подтверждение отмены — нажмите кнопку.", intent="cancel")
        ),
    ]
    body = await _simulate(admin_client, ctx, "Отмените мою запись, не смогу прийти", WA_PHONE)
    assert body["outcome"] == "sent", body
    proposed = _tool_result(fake.calls[2]["parts"], "propose_cancel")
    assert proposed["kind"] == "cancel" and proposed["buttonSent"] is True
    assert (await _appointment(_resources, appointment_id)).status is AppointmentStatus.SCHEDULED

    pressed = await _press(admin_client, ctx, WA_PHONE, proposed["confirmationId"])
    assert any("отменена" in (m["body"] or "") for m in pressed["messages"])
    cancelled = await _appointment(_resources, appointment_id)
    assert cancelled.status is AppointmentStatus.CANCELLED
    assert cancelled.cancel_reason is CancelReason.PATIENT_REQUEST
    assert "WhatsApp-ассистента" in (cancelled.cancel_comment or "")

    again = await _press(admin_client, ctx, WA_PHONE, proposed["confirmationId"])
    assert any("уже отменена" in (m["body"] or "") for m in again["messages"])


async def test_only_own_future_changeable_appointments(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Чужая, прошедшая и уже начатая записи — отказ данными модели, без резерва."""
    await _set_mode(admin_client, ctx, "auto")
    foreign_id, _ = await _book(
        client, ctx, phone="0700700501", first_name="Бермет", slot=_workday(14)
    )
    past_id, _ = await _book(
        client, ctx, phone=LOCAL_PHONE, first_name="Айгуль", slot=_workday(10, days_ahead=-3)
    )

    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(name="propose_cancel", args={"appointmentId": foreign_id})
        ),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_reschedule", args={"appointmentId": past_id, "startsAt": _workday(11)}
            )
        ),
        AiResult(text=_reply_json("Уточню у администратора.", needs_human=True)),
    ]
    body = await _simulate(admin_client, ctx, "Отмените запись", WA_PHONE)
    assert body["outcome"] == "escalated"
    assert (
        "Такой записи у пациента нет"
        in _tool_result(fake.calls[1]["parts"], "propose_cancel")["error"]
    )
    assert "уже прошла" in _tool_result(fake.calls[2]["parts"], "propose_reschedule")["error"]
    async with _resources.factory() as session:
        assert (await session.execute(select(SlotHold))).scalars().all() == []
    assert (await _appointment(_resources, foreign_id)).status is AppointmentStatus.SCHEDULED


async def test_button_after_appointment_started_declines_politely(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Пока пациент думал, администратор отметил «пришёл»: кнопка не отменяет визит."""
    await _set_mode(admin_client, ctx, "auto")
    appointment_id, _ = await _book(
        client,
        ctx,
        phone=LOCAL_PHONE,
        first_name="Айгуль",
        slot=_workday(10),
    )
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(name="propose_cancel", args={"appointmentId": appointment_id})
        ),
        AiResult(text=_reply_json("Отправил подтверждение отмены.", intent="cancel")),
    ]
    body = await _simulate(admin_client, ctx, "Отмените запись", WA_PHONE)
    proposed = _tool_result(fake.calls[1]["parts"], "propose_cancel")
    assert body["outcome"] == "sent" and proposed["buttonSent"] is True

    arrived = await client.patch(
        f"{API}/clinic/appointments/{appointment_id}",
        headers=ctx.owner_headers,
        json={"status": "arrived"},
    )
    assert arrived.status_code == 200, arrived.text

    pressed = await _press(admin_client, ctx, WA_PHONE, proposed["confirmationId"])
    assert any("уже изменилась" in (m["body"] or "") for m in pressed["messages"])
    assert (await _appointment(_resources, appointment_id)).status is AppointmentStatus.ARRIVED
