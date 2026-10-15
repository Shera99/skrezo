"""Интеграционные тесты доработок разговора ассистента (решения 15.10.2026).

По переписке 13.10.2026:

* передача человеку — текст по причине («позвал администратора» на просьбу
  пациента), без одной и той же фразы подряд, а вне рабочих часов — когда
  клиника снова на связи;
* жалоба, номер зуба и вторая процедура словами пациента ложатся в
  комментарий записи;
* пока модель думает, пациент видит «печатает…», а его сообщение —
  прочитанным; только в режиме ``auto``.

Отсчёт автовозврата боту — в ``test_ai_fixes_api.py`` рядом с остальными
тестами возврата, тон промпта — в регрессионном наборе (prm-009, prm-010).
"""

from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.modules.ai.application.booking import NOTE_MAX_LEN
from app.shared.ai import AiResult, ToolCallRequest
from app.shared.db.enums import AiMessageRole
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.event import Event
from app.shared.db.models.slot_hold import SlotHold
from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient
from tests.api.conftest import Ctx, _FakeProvider, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources, own_membership_id

_seq = itertools.count(1)
_BISHKEK = ZoneInfo("Asia/Bishkek")

_ASKED = "Позвал администратора — он ответит вам здесь."
_WAITING = "Администратор уже в курсе — ответит здесь."


def _patient() -> str:
    return f"+99670051{next(_seq):04d}"


def _tomorrow_at(hour: int) -> str:
    day = datetime.now(_BISHKEK).date() + timedelta(days=1)
    return f"{day.isoformat()}T{hour:02d}:00:00+06:00"


async def _set_hours(client: AsyncClient, ctx: Ctx, *, closed_today: bool = False) -> None:
    """Круглосуточная клиника; ``closed_today`` — сегодня выходной, завтра с 09:00."""
    today = datetime.now(_BISHKEK).weekday()
    opens, closes = ("09:00:00", "18:00:00") if closed_today else ("00:00:00", "23:59:59")
    days = [
        {
            "weekday": weekday,
            "opensAt": opens,
            "closesAt": closes,
            "isWorking": not (closed_today and weekday == today),
        }
        for weekday in range(7)
    ]
    resp = await client.put(
        f"{API}/clinic/schedule/working-hours", headers=ctx.owner_headers, json={"days": days}
    )
    assert resp.status_code == 200, resp.text


async def _service_messages(res: Resources, conversation_id: uuid.UUID) -> list[str]:
    async with res.factory() as session:
        return list(
            (
                await session.execute(
                    select(AiMessage.body)
                    .where(
                        AiMessage.conversation_id == conversation_id,
                        AiMessage.role == AiMessageRole.SYSTEM,
                    )
                    .order_by(AiMessage.id)
                )
            ).scalars()
        )


async def _ask_human_again(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider, phone: str
) -> Any:
    """Пациент просит человека, модель передаёт диалог, сотрудник возвращает его боту."""
    conversation_id = (await _simulate_reply(admin_client, ctx, fake, phone))["conversationId"]
    returned = await client.post(
        f"{API}/clinic/conversations/{conversation_id}/assistant",
        headers=ctx.owner_headers,
        json={"active": True},
    )
    assert returned.status_code == 200, returned.text
    return conversation_id


async def _simulate_reply(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, phone: str
) -> Any:
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="escalate", args={"reason": "patient_asked"}))
    ]
    body = await _simulate(admin_client, ctx, "Позовите администратора", phone)
    assert body["outcome"] == "escalated", body
    return body


# ── 6. Передача человеку: текст по причине, без повторов, с часами работы ───


async def test_patient_asked_for_human_gets_called_not_check_back(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """«Позовите администратора» — «позвал администратора», а не «уточню и вернусь»."""
    await _set_hours(client, ctx)
    await _set_mode(admin_client, ctx, "auto")

    body = await _simulate_reply(admin_client, ctx, fake, _patient())

    assert await _service_messages(_resources, uuid.UUID(body["conversationId"])) == [_ASKED]


async def test_repeated_handoff_says_waiting_then_stays_silent(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Инцидент 13.10: одна фраза шесть раз подряд. Теперь — «уже в курсе», потом тишина."""
    await _set_hours(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _ask_human_again(admin_client, client, ctx, fake, phone)
    await _ask_human_again(admin_client, client, ctx, fake, phone)
    await _simulate_reply(admin_client, ctx, fake, phone)

    assert await _service_messages(_resources, uuid.UUID(conversation_id)) == [_ASKED, _WAITING]


async def test_handoff_after_hours_tells_when_clinic_opens(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Сегодня выходной — пациент узнаёт, что ответят завтра с 09:00."""
    await _set_hours(client, ctx, closed_today=True)
    await _set_mode(admin_client, ctx, "auto")

    body = await _simulate_reply(admin_client, ctx, fake, _patient())

    [notice] = await _service_messages(_resources, uuid.UUID(body["conversationId"]))
    assert notice == (f"{_ASKED} Сейчас клиника закрыта — ответим в рабочее время: завтра с 09:00.")


# ── 9. Жалоба и пожелание пациента — в комментарий записи ───────────────────


async def test_patient_note_goes_to_appointment_comment(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Инцидент 13.10: «18 зуб болит», «и чистку» — врач видит это в записи."""
    await _set_hours(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    phone = _patient()
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айбек"})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={
                    "doctorId": doctor_id,
                    "startsAt": _tomorrow_at(10),
                    "note": "  болит 18 зуб,\n ещё хочет чистку ",
                },
            )
        ),
        AiResult(text=_reply_json("Отправил подтверждение — нажмите кнопку.", intent="booking")),
    ]
    body = await _simulate(
        admin_client, ctx, "Хочу полечить 18 зуб, запишите завтра в 10, я Айбек", phone
    )
    assert body["outcome"] == "sent", body
    async with _resources.factory() as session:
        hold = (await session.execute(select(SlotHold))).scalar_one()
    assert hold.note == "болит 18 зуб, ещё хочет чистку"  # пробелы и переносы схлопнуты

    pressed = await admin_client.post(
        f"{API}/admin/ai/simulate-inbound",
        json={
            "clinicId": ctx.clinic_id,
            "fromPhone": phone,
            "text": "-",
            "buttonPayload": f"hold:{hold.id}",
        },
    )
    assert pressed.status_code == 200, pressed.text
    async with _resources.factory() as session:
        appointment = (await session.execute(select(Appointment))).scalar_one()
    assert appointment.comment == (
        "Запись через WhatsApp-ассистента. Со слов пациента (записал ассистент): "
        "болит 18 зуб, ещё хочет чистку"
    )


async def test_long_note_is_cut(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Комментарий врачу — не переписка: длинный пересказ обрезается."""
    await _set_hours(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айбек"})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={"doctorId": doctor_id, "startsAt": _tomorrow_at(11), "note": "а" * 1000},
            )
        ),
        AiResult(text=_reply_json("Отправил подтверждение — нажмите кнопку.", intent="booking")),
    ]
    await _simulate(admin_client, ctx, "Запишите меня, я Айбек", _patient())

    async with _resources.factory() as session:
        hold = (await session.execute(select(SlotHold))).scalar_one()
    assert hold.note == "а" * NOTE_MAX_LEN


# ── 10. «Печатает…» и «прочитано», пока модель думает ───────────────────────


@pytest.fixture
def typing_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def record(self: Any, phone_number_id: str, message_id: str) -> None:
        calls.append((phone_number_id, message_id))

    monkeypatch.setattr(LoggingWhatsAppClient, "send_typing", record)
    return calls


async def test_typing_is_shown_while_model_thinks(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    typing_calls: list[tuple[str, str]],
) -> None:
    """В auto — одно «печатает…» на сообщение пациента, по его wamid."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?"))]

    body = await _simulate(admin_client, ctx, "Здравствуйте", _patient())

    async with _resources.factory() as session:
        wamid = (
            await session.execute(
                select(AiMessage.wa_message_id).where(
                    AiMessage.conversation_id == uuid.UUID(body["conversationId"]),
                    AiMessage.role == AiMessageRole.PATIENT,
                )
            )
        ).scalar_one()
    assert typing_calls == [(ctx.phone_number_id, wamid)]


@pytest.mark.parametrize("mode", ["assisted", "shadow"])
async def test_no_typing_when_human_answers(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    typing_calls: list[tuple[str, str]],
    mode: str,
) -> None:
    """В «Проверяю» и теневом режиме отвечает человек — «прочитано ботом» было бы неправдой."""
    await _set_mode(admin_client, ctx, mode)
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?"))]

    await _simulate(admin_client, ctx, "Здравствуйте", _patient())

    assert typing_calls == []


async def test_no_typing_for_medical_template(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    typing_calls: list[tuple[str, str]],
) -> None:
    """Шаблон медфильтра уходит сразу, без модели, — «печатает…» не нужно."""
    await _set_mode(admin_client, ctx, "auto")

    await _simulate(admin_client, ctx, "У меня сильно болит зуб", _patient())

    assert typing_calls == []


# ── Находки ревью: эпизоды, сброс цепочки, неотложное у администратора ─────


async def test_bot_reply_between_handoffs_resets_the_chain(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Между передачами бот ответил сам — новая передача снова полная, не «уже в курсе»."""
    await _set_hours(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _ask_human_again(admin_client, client, ctx, fake, phone)
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)

    await _simulate_reply(admin_client, ctx, fake, phone)

    assert await _service_messages(_resources, uuid.UUID(conversation_id)) == [_ASKED, _ASKED]


async def test_handoff_of_new_episode_is_full_again(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """«Уже в курсе» месячной давности не глушит передачу по новому обращению."""
    await _set_hours(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = uuid.UUID(await _ask_human_again(admin_client, client, ctx, fake, phone))
    await _ask_human_again(admin_client, client, ctx, fake, phone)  # «уже в курсе»
    async with _resources.factory() as session:
        await session.execute(
            update(AiMessage)
            .where(AiMessage.conversation_id == conversation_id)
            .values(sent_at=AiMessage.sent_at - timedelta(days=30))
        )
        await session.commit()

    await _simulate_reply(admin_client, ctx, fake, phone)

    assert await _service_messages(_resources, conversation_id) == [_ASKED, _WAITING, _ASKED]


async def test_urgent_message_in_escalated_dialog_is_answered_at_once(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Диалог у администратора, пациент пишет о кровотечении — телефон клиники сразу.

    Автовозврат ждёт 15 минут с сообщения пациента — неотложное ждать не может.
    """
    await _set_hours(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    body = await _simulate_reply(admin_client, ctx, fake, phone)
    conversation_id = uuid.UUID(body["conversationId"])

    quiet = await _simulate(admin_client, ctx, "Ну что там?", phone)
    urgent = await _simulate(admin_client, ctx, "После удаления сильное кровотечение", phone)

    assert quiet["outcome"] == "skipped"  # обычное сообщение ждёт человека
    assert urgent["outcome"] == "escalated"
    assert urgent["escalationReason"] == "urgent_medical"
    async with _resources.factory() as session:
        bot = (
            await session.execute(
                select(AiMessage.body).where(
                    AiMessage.conversation_id == conversation_id,
                    AiMessage.role == AiMessageRole.BOT,
                )
            )
        ).scalar_one()
        events = (
            await session.execute(
                select(func.count())
                .select_from(Event)
                .where(Event.type == "ai.urgent", Event.entity_id == conversation_id)
            )
        ).scalar_one()
    assert bot.startswith("Это похоже на неотложное")
    assert events == 1


async def test_note_with_number_patient_never_wrote_is_refused(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Пациент писал «8 зуб», модель пересказала «18 зуб» — неверный зуб в запись не идёт."""
    await _set_hours(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айбек"})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={"doctorId": doctor_id, "startsAt": _tomorrow_at(10), "note": "18 зуб"},
            )
        ),
        AiResult(text=_reply_json("Уточните, пожалуйста, какой зуб беспокоит?")),
    ]

    await _simulate(admin_client, ctx, "Хочу полечить 8 зуб, я Айбек", _patient())

    refused = next(
        p.tool_result
        for p in fake.calls[-1]["parts"]
        if p.role == "tool" and p.tool_name == "propose_appointment"
    )
    assert "18" in refused["error"] and "buttonSent" not in refused
    async with _resources.factory() as session:
        assert (await session.execute(select(SlotHold))).scalar_one_or_none() is None
