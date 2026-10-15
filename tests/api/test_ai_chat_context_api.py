"""Интеграционные тесты исправлений ассистента по переписке 13.10.2026.

Каждый тест — сценарий из живой переписки, где бот ошибся:

* модель видит служебные сообщения клиники — кнопку подтверждения, «готово,
  вы записаны», «уточню у администратора» (раньше не видела и предлагала
  вторую запись, а на «Здравствуйте» снова звала человека);
* сообщение с кнопкой подтверждения сохраняется в переписке;
* вторая запись пациенту, у которого уже есть будущая, не уходит молча;
* свой врач с именем, совпавшим с названием чужой клиники, — не V09, а
  настоящее упоминание чужой клиники — сигнал без блока;
* кнопка ушла, а текст снят валидатором — подсказка нажать кнопку вместо
  «уточню у администратора»;
* жалоба и сильная боль у пациента, записанного на завтра, — напоминание его
  записи, а не новое время «как незнакомцу».

Через боевой конвейер (``/admin/ai/simulate-inbound``), модель — сценарий.
"""

from __future__ import annotations

import itertools
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import func, select

from app.main import app
from app.modules.ai.application.booking import BookingService
from app.modules.ai.presentation.deps import get_ai_provider
from app.shared.ai import AiMessagePart, AiResult, ToolCallRequest
from app.shared.ai.provider import AiProviderUnavailableError
from app.shared.db.enums import AiConversationStatus, AiMessageRole
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.event import Event
from app.shared.db.models.slot_hold import SlotHold
from tests.api.conftest import Ctx, _FakeProvider, _phone, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources, own_membership_id

_seq = itertools.count(1)
_BISHKEK = ZoneInfo("Asia/Bishkek")

_FALLBACK = "Отправил подтверждение отдельным сообщением — нажмите в нём «Подтвердить»."
_ESCALATION_NOTICE = "Уточню у администратора и вернусь с ответом."


def _patient() -> str:
    return f"+99670041{next(_seq):04d}"


def _tomorrow_at(hour: int) -> str:
    """Завтрашний час по часам клиники (Бишкек) — ISO, как в выдаче слотов."""
    day = datetime.now(_BISHKEK).date() + timedelta(days=1)
    return f"{day.isoformat()}T{hour:02d}:00:00+06:00"


async def _around_the_clock(client: AsyncClient, ctx: Ctx) -> None:
    """Клиника работает круглосуточно: тест не зависит от времени суток и дня."""
    days = [
        {"weekday": d, "opensAt": "00:00:00", "closesAt": "23:59:59", "isWorking": True}
        for d in range(7)
    ]
    resp = await client.put(
        f"{API}/clinic/schedule/working-hours", headers=ctx.owner_headers, json={"days": days}
    )
    assert resp.status_code == 200, resp.text


async def _press_button(admin_client: AsyncClient, ctx: Ctx, phone: str, payload: str) -> Any:
    resp = await admin_client.post(
        f"{API}/admin/ai/simulate-inbound",
        json={"clinicId": ctx.clinic_id, "fromPhone": phone, "text": "-", "buttonPayload": payload},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _book(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    res: Resources,
    phone: str,
    starts_at: str,
) -> uuid.UUID:
    """Записать пациента через бота: карточка → кнопка → нажатие. Вернуть id диалога."""
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айбек"})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment", args={"doctorId": doctor_id, "startsAt": starts_at}
            )
        ),
        AiResult(text=_reply_json("Отправил подтверждение — нажмите кнопку.", intent="booking")),
    ]
    body = await _simulate(admin_client, ctx, "Запишите меня, я Айбек", phone)
    assert body["outcome"] == "sent", body
    conversation_id = uuid.UUID(body["conversationId"])
    async with res.factory() as session:
        hold = (
            await session.execute(
                select(SlotHold).where(SlotHold.conversation_id == conversation_id)
            )
        ).scalar_one()
    pressed = await _press_button(admin_client, ctx, phone, f"hold:{hold.id}")
    assert any("вы записаны" in (m["body"] or "") for m in pressed["messages"]), pressed
    return conversation_id


def _model_texts(parts: list[AiMessagePart]) -> list[str]:
    """Реплики «нашей стороны», которые модель получила в истории хода."""
    return [part.text for part in parts if part.role == "model" and part.text]


def _tool_result(parts: list[AiMessagePart], tool: str) -> Any:
    return next(p for p in parts if p.role == "tool" and p.tool_name == tool).tool_result


async def _messages(
    res: Resources, conversation_id: uuid.UUID, role: AiMessageRole
) -> list[AiMessage]:
    async with res.factory() as session:
        return list(
            (
                await session.execute(
                    select(AiMessage)
                    .where(AiMessage.conversation_id == conversation_id, AiMessage.role == role)
                    .order_by(AiMessage.id)
                )
            ).scalars()
        )


async def _holds(res: Resources, conversation_id: uuid.UUID) -> int:
    async with res.factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(SlotHold)
                    .where(SlotHold.conversation_id == conversation_id)
                )
            ).scalar_one()
        )


# ── 1. Служебные сообщения клиники — в истории модели ───────────────────────


async def test_model_sees_button_and_booking_confirmation(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Кнопка и «готово, вы записаны» сохраняются в переписке и доходят до модели."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _book(
        admin_client, client, ctx, fake, _resources, phone, _tomorrow_at(10)
    )

    fake.script = []
    await _simulate(admin_client, ctx, "Спасибо! А я точно записан?", phone)

    seen = _model_texts(fake.calls[-1]["parts"])
    assert any(text.startswith("Проверьте запись") for text in seen), seen
    assert any("вы записаны" in text for text in seen), seen
    # Кнопка — строка переписки со своим wamid: её видят чат и галочки доставки.
    service = await _messages(_resources, conversation_id, AiMessageRole.SYSTEM)
    button = next(m for m in service if m.body.startswith("Проверьте запись"))
    assert button.wa_message_id and button.sent_at is not None


async def test_model_sees_handoff_notice_after_return_to_bot(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """После возврата боту модель видит, что «позовите админа» уже обработано."""
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="escalate", args={"reason": "patient_asked"}))
    ]
    body = await _simulate(admin_client, ctx, "Позовите администратора", phone)
    assert body["outcome"] == "escalated"
    returned = await client.post(
        f"{API}/clinic/conversations/{body['conversationId']}/assistant",
        headers=ctx.owner_headers,
        json={"active": True},
    )
    assert returned.status_code == 200, returned.text

    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?"))]
    again = await _simulate(admin_client, ctx, "Здравствуйте", phone)

    assert again["outcome"] == "sent"
    assert _ESCALATION_NOTICE in _model_texts(fake.calls[-1]["parts"])
    assert "Снова звать человека — только по новой причине" in fake.calls[-1]["system"]


# ── 2. Вторая запись — не молча ─────────────────────────────────────────────


async def test_second_booking_needs_patient_consent(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Пациент с записью на завтра «хочет записаться» — модель узнаёт о записи, кнопки нет."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _book(
        admin_client, client, ctx, fake, _resources, phone, _tomorrow_at(10)
    )
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    buttons_before = len(await _messages(_resources, conversation_id, AiMessageRole.SYSTEM))

    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={"doctorId": doctor_id, "startsAt": _tomorrow_at(11)},
            )
        ),
        AiResult(text=_reply_json("У вас уже есть запись. Оставить её или нужна ещё одна?")),
    ]
    await _simulate(admin_client, ctx, "Я хотел записаться броо", phone)

    refused = _tool_result(fake.calls[-1]["parts"], "propose_appointment")
    assert "buttonSent" not in refused
    [existing] = refused["existingAppointments"]
    assert existing["time"] == "10:00"
    assert existing["startsAt"] == _tomorrow_at(10)
    assert await _holds(_resources, conversation_id) == 1  # нового резерва нет
    service = await _messages(_resources, conversation_id, AiMessageRole.SYSTEM)
    assert len(service) == buttons_before  # и новой кнопки тоже

    # Пациент подтвердил, что нужна ещё одна запись, — кнопка уходит.
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={"doctorId": doctor_id, "startsAt": _tomorrow_at(11), "secondBooking": True},
            )
        ),
        AiResult(text=_reply_json("Отправил подтверждение — нажмите кнопку.", intent="booking")),
    ]
    await _simulate(admin_client, ctx, "Да, нужна ещё одна", phone)

    assert _tool_result(fake.calls[-1]["parts"], "propose_appointment")["buttonSent"] is True
    assert await _holds(_resources, conversation_id) == 2


# ── 3. V09: свой врач — не чужая клиника; чужая — сигнал без блока ──────────


async def _other_clinic(admin_client: AsyncClient, name: str) -> None:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    resp = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Чужой Владелец",
            "adminPhone": _phone(),
            "name": name,
            "cityId": city,
            "businessPhone": "0700000001",
            "seats": 3,
        },
    )
    assert resp.status_code == 201, resp.text


async def test_own_doctor_named_like_other_clinic_is_not_v09(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Инцидент 13.10: врач «Aibek» и чужая клиника «Aibek» — ответ уходит пациенту."""
    await own_membership_id(client, ctx.owner_headers)  # врач «Врач Тестовый»
    await _other_clinic(admin_client, "Тестовый")
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Вас примет врач Тестовый. Подобрать время?"))]

    body = await _simulate(admin_client, ctx, "А кто примет?", _patient())

    assert body["outcome"] == "sent", body
    [reply] = await _messages(_resources, uuid.UUID(body["conversationId"]), AiMessageRole.BOT)
    assert reply.sent_at is not None
    assert "signals" not in (reply.validator or {})


async def test_other_clinic_mention_is_a_signal_not_a_block(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Решение 14.10: чужая клиника в ответе — сигнал нам, пациент ответ получает."""
    await _other_clinic(admin_client, "Стоматология Аврора")
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Попробуйте Стоматология Аврора, там дешевле."))]

    body = await _simulate(admin_client, ctx, "А где ещё лечат?", _patient())

    assert body["outcome"] == "sent", body
    [reply] = await _messages(_resources, uuid.UUID(body["conversationId"]), AiMessageRole.BOT)
    assert reply.validator is not None
    assert reply.validator["passed"] is True
    assert "стоматология аврора" in reply.validator["signals"]["V09"]


# ── 4. Кнопка ушла, текст снят — подсказка, а не передача ───────────────────


async def test_blocked_text_after_button_sends_hint_not_handoff(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Инцидент 13.10: кнопка у пациента — «нажмите «Подтвердить»», диалог остаётся у бота."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    invented = _reply_json("Записал вас, стоимость 9 999 сом.", intent="booking", facts=["s"])
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айбек"})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={"doctorId": doctor_id, "startsAt": _tomorrow_at(10)},
            )
        ),
        AiResult(text=invented),
        AiResult(text=invented),  # повтор с подсказкой тоже выдумал цену
    ]

    body = await _simulate(admin_client, ctx, "Самое ближайшее", _patient())

    assert body["outcome"] == "sent", body
    conversation_id = uuid.UUID(body["conversationId"])
    blocked, hint = await _messages(_resources, conversation_id, AiMessageRole.BOT)
    assert blocked.sent_at is None
    assert (blocked.validator or {}).get("codes") == ["V01"]
    assert hint.body == _FALLBACK and hint.sent_at is not None
    assert hint.validator == {"passed": True, "codes": [], "notes": "button_fallback"}
    async with _resources.factory() as session:
        conversation = await session.get(AiConversation, conversation_id)
        assert conversation is not None
        assert conversation.status is AiConversationStatus.ACTIVE
        escalations = (
            await session.execute(
                select(AiEscalation).where(AiEscalation.conversation_id == conversation_id)
            )
        ).scalars()
        assert list(escalations) == []


# ── 5. Жалоба и боль у пациента с записью ───────────────────────────────────


async def _last_bot_text(res: Resources, conversation_id: uuid.UUID) -> str:
    return (await _messages(res, conversation_id, AiMessageRole.BOT))[-1].body


async def test_acute_pain_of_booked_patient_reminds_visit(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Инцидент 13.10: «сильно болит» от записанного — его запись и перенос раньше."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _book(
        admin_client, client, ctx, fake, _resources, phone, _tomorrow_at(10)
    )

    body = await _simulate(admin_client, ctx, "У меня сильно болит зуб бро", phone)

    assert body["outcome"] == "sent", body
    text = await _last_bot_text(_resources, conversation_id)
    assert "Ваша запись: завтра в 10:00" in text, text
    # Круглосуточная клиника: свободное время раньше завтрашних 10:00 есть всегда.
    assert text.endswith("Перенести запись?"), text
    async with _resources.factory() as session:
        appointment = (
            await session.execute(select(Appointment).where(Appointment.clinic_id == ctx.clinic_id))
        ).scalar_one()
        [event] = (
            await session.execute(
                select(Event).where(
                    Event.type == "ai.urgent_booking", Event.entity_id == conversation_id
                )
            )
        ).scalars()
    assert event.payload["appointmentId"] == str(appointment.id)
    assert body["escalationReason"] is None  # диалог у бота


async def test_complaint_of_booked_patient_reminds_visit(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Жалоба от записанного на завтра — напоминание записи вместо «ближайшее время»."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _book(
        admin_client, client, ctx, fake, _resources, phone, _tomorrow_at(10)
    )

    await _simulate(admin_client, ctx, "У меня 18 суб болит хотел лечить", phone)

    text = await _last_bot_text(_resources, conversation_id)
    assert "Ваша запись: завтра в 10:00" in text, text
    assert "Ближайшее время" not in text


# ── 6. Находки ревью: обходы и пути после кнопки ────────────────────────────


def _day_at(days: int, hour: int) -> str:
    """Час через ``days`` дней по часам клиники — ISO, как в выдаче слотов."""
    day = datetime.now(_BISHKEK).date() + timedelta(days=days)
    return f"{day.isoformat()}T{hour:02d}:00:00+06:00"


def _tool_results(parts: list[AiMessagePart], tool: str) -> list[Any]:
    return [p.tool_result for p in parts if p.role == "tool" and p.tool_name == tool]


def _propose(doctor_id: str, starts_at: str, **extra: Any) -> AiResult:
    return AiResult(
        tool_call=ToolCallRequest(
            name="propose_appointment",
            args={"doctorId": doctor_id, "startsAt": starts_at, **extra},
        )
    )


async def _escalations(res: Resources, conversation_id: uuid.UUID) -> list[AiEscalation]:
    async with res.factory() as session:
        return list(
            (
                await session.execute(
                    select(AiEscalation).where(AiEscalation.conversation_id == conversation_id)
                )
            ).scalars()
        )


async def test_second_booking_flag_in_the_same_turn_is_refused(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Модель узнала о записи и сразу «исправилась» secondBooking=true — кнопки нет."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _book(
        admin_client, client, ctx, fake, _resources, phone, _tomorrow_at(10)
    )
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    fake.script = [
        _propose(doctor_id, _tomorrow_at(11)),
        _propose(doctor_id, _tomorrow_at(11), secondBooking=True),
        AiResult(text=_reply_json("У вас уже есть запись. Нужна ещё одна?")),
    ]

    await _simulate(admin_client, ctx, "Я хотел записаться броо", phone)

    first, second = _tool_results(fake.calls[-1]["parts"], "propose_appointment")
    assert "existingAppointments" in first
    assert "secondBooking" in second["error"] and "buttonSent" not in second
    assert await _holds(_resources, conversation_id) == 1


async def test_far_future_visit_does_not_block_booking(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Плановый визит через пять дней не мешает записи на завтра — кнопка уходит."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _book(
        admin_client, client, ctx, fake, _resources, phone, _day_at(5, 10)
    )
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    fake.script = [
        _propose(doctor_id, _tomorrow_at(10)),
        AiResult(text=_reply_json("Отправил подтверждение — нажмите кнопку.", intent="booking")),
    ]

    await _simulate(admin_client, ctx, "Можно завтра пораньше?", phone)

    assert _tool_result(fake.calls[-1]["parts"], "propose_appointment")["buttonSent"] is True
    assert await _holds(_resources, conversation_id) == 2


async def test_wrong_language_twice_after_button_sends_hint(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Кнопка ушла, модель дважды ответила по-русски кыргызскому пациенту — подсказка."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    russian = _reply_json("Отправил подтверждение — нажмите кнопку.", intent="booking")
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айбек"})),
        _propose(doctor_id, _tomorrow_at(10)),
        AiResult(text=russian),
        AiResult(text=russian),
    ]

    body = await _simulate(
        admin_client, ctx, "Саламатсызбы! Эртең саат 10го жазып коюңузчу", _patient()
    )

    assert body["outcome"] == "sent", body
    conversation_id = uuid.UUID(body["conversationId"])
    hint = (await _messages(_resources, conversation_id, AiMessageRole.BOT))[-1]
    assert hint.body == (
        "Ырастоону өзүнчө билдирүү менен жөнөттүм — андагы «Ырастоо» баскычын басыңыз."
    )
    assert await _escalations(_resources, conversation_id) == []


@dataclass
class _UnavailableAfterButton(_FakeProvider):
    """Третий вызов модели (после кнопки) — временно недоступна."""

    async def complete(self, **kwargs: Any) -> AiResult:
        if len(self.calls) == 2:
            self.calls.append(kwargs)
            raise AiProviderUnavailableError("Gemini: HTTP 429")
        return await super().complete(**kwargs)


async def test_provider_unavailable_after_button_sends_hint(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    _resources: Resources,
) -> None:
    """Модель недоступна после кнопки — подсказка сразу, а не отложенный ход.

    Отложенный ход пропустился бы как отвеченный (кнопка — наш ход), а на
    последней попытке ответил бы «уточню у администратора» при живой кнопке.
    """
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    provider = _UnavailableAfterButton(
        script=[
            AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айбек"})),
            _propose(doctor_id, _tomorrow_at(10)),
        ]
    )
    app.dependency_overrides[get_ai_provider] = lambda: provider
    try:
        body = await _simulate(admin_client, ctx, "Запишите на завтра в 10", _patient())
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)

    assert body["outcome"] == "sent", body
    conversation_id = uuid.UUID(body["conversationId"])
    hint = (await _messages(_resources, conversation_id, AiMessageRole.BOT))[-1]
    assert hint.body == _FALLBACK
    assert await _escalations(_resources, conversation_id) == []


async def test_explicit_escalate_after_button_still_hands_over(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Модель сама позвала человека после кнопки — это её решение, а не сбой хода."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айбек"})),
        _propose(doctor_id, _tomorrow_at(10)),
        AiResult(tool_call=ToolCallRequest(name="escalate", args={"reason": "patient_asked"})),
    ]

    body = await _simulate(admin_client, ctx, "Запишите и позовите администратора", _patient())

    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "patient_asked"


async def test_first_attempt_v09_signal_survives_clean_retry(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Выдумка с чужой клиникой, исправленная повтором, всё равно видна в журнале."""
    await _other_clinic(admin_client, "Стоматология Аврора")
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(text=_reply_json("В «Стоматология Аврора» чистка за 9 900 сом.", facts=["s"])),
        AiResult(text=_reply_json("Подскажите, на какую услугу вас записать?")),
    ]

    body = await _simulate(admin_client, ctx, "Сколько стоит чистка?", _patient())

    assert body["outcome"] == "sent", body
    [reply] = await _messages(_resources, uuid.UUID(body["conversationId"]), AiMessageRole.BOT)
    assert reply.validator is not None
    assert reply.validator["retriedCodes"] == ["V01"]
    assert "V09" in reply.validator["signals"]


async def test_visit_after_tomorrow_does_not_change_pain_template(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Запись послезавтра — с сильной болью предлагаем время раньше, как обычно."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _book(
        admin_client, client, ctx, fake, _resources, phone, _day_at(2, 10)
    )

    await _simulate(admin_client, ctx, "У меня сильно болит зуб бро", phone)

    text = await _last_bot_text(_resources, conversation_id)
    assert "Ближайшее время" in text and "Ваша запись" not in text, text


async def test_pain_template_for_booked_patient_in_kyrgyz(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Кыргызча жазган пациент — шаблон да кыргызча."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _book(
        admin_client, client, ctx, fake, _resources, phone, _tomorrow_at(10)
    )

    await _simulate(admin_client, ctx, "Тишим катуу ооруп жатат", phone)

    text = await _last_bot_text(_resources, conversation_id)
    assert "Сиздин жазылууңуз: эртең саат 10:00, дарыгер" in text, text
    assert text.endswith("Жазылууну котороюнбу?"), text


async def test_pain_of_booked_patient_in_shadow_mode_goes_to_human(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Теневой режим: пациенту ничего не ушло — сильная боль сразу у администратора."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    await _book(admin_client, client, ctx, fake, _resources, phone, _tomorrow_at(10))
    await _set_mode(admin_client, ctx, "shadow")

    body = await _simulate(admin_client, ctx, "У меня сильно болит зуб бро", phone)

    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "acute_pain"


async def test_visits_of_patient_are_not_seen_by_other_clinic(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Пациент глобальный, но его запись в одной клинике другой клинике не видна."""
    await _around_the_clock(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    conversation_id = await _book(
        admin_client, client, ctx, fake, _resources, phone, _tomorrow_at(10)
    )
    await _other_clinic(admin_client, "Стоматология Соседняя")
    async with _resources.factory() as session:
        conversation = await session.get(AiConversation, conversation_id)
        assert conversation is not None and conversation.patient_id is not None
        other_id = (
            await session.execute(select(Clinic.id).where(Clinic.name == "Стоматология Соседняя"))
        ).scalar_one()
        booking = BookingService(session)
        own = await booking.upcoming_visits(conversation.clinic_id, conversation.patient_id)
        other = await booking.upcoming_visits(other_id, conversation.patient_id)

    assert len(own) == 1
    assert other == []
