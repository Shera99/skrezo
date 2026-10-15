"""Интеграционные тесты исправлений ассистента по переписке 12.10.2026.

Через боевой конвейер (``/admin/ai/simulate-inbound``) и задачу воркера:

* в каждом ходе модель получает строку «СЕЙЧАС» с датой и временем клиники;
* исправимый блок валидатора — один повтор с подсказкой, а не мгновенная
  передача администратору;
* цифры и «Доктор …» в названии своей клиники не превращают приветствие в
  эскалацию; повтор, приславший не JSON, не прячет настоящую причину блока;
* передача человеку и шаблоны медфильтра шлют сигналы сокета — открытый чат
  видит новый статус и сообщение без обновления экрана;
* кыргызское сообщение переключает язык ответа и служебных шаблонов;
* сильная зубная боль: ближайшее время + уведомление администратору, диалог
  остаётся у бота;
* инструмент слотов не отдаёт прошлое;
* пациент пишет в диалог у администратора — напоминание (не чаще раза в 5 мин);
* через 15 минут без ответа сотрудника диалог возвращается боту.
"""

from __future__ import annotations

import itertools
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.jobs.tasks import ai as ai_tasks
from app.modules.ai.application import reply_service
from app.shared.ai import AiResult, ToolCallRequest
from app.shared.db.enums import AiConversationStatus, AiMessageRole, Lang
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_tool_call import AiToolCall
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.event import Event
from tests.api.conftest import Ctx, _FakeProvider, _patch_job, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources, own_membership_id

_seq = itertools.count(1)
_BISHKEK = ZoneInfo("Asia/Bishkek")


def _patient() -> str:
    return f"+99670031{next(_seq):04d}"


async def _conversation(res: Resources, phone: str) -> AiConversation:
    async with res.factory() as session:
        return (
            await session.execute(select(AiConversation).where(AiConversation.wa_phone == phone))
        ).scalar_one()


async def _events(res: Resources, type_: str, conversation_id: uuid.UUID) -> list[Event]:
    async with res.factory() as session:
        return list(
            (
                await session.execute(
                    select(Event).where(Event.type == type_, Event.entity_id == conversation_id)
                )
            ).scalars()
        )


async def _bot_messages(res: Resources, conversation_id: uuid.UUID) -> list[AiMessage]:
    async with res.factory() as session:
        return list(
            (
                await session.execute(
                    select(AiMessage)
                    .where(
                        AiMessage.conversation_id == conversation_id,
                        AiMessage.role == AiMessageRole.BOT,
                    )
                    .order_by(AiMessage.id)
                )
            ).scalars()
        )


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


async def _escalate(admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, phone: str) -> Any:
    """Довести диалог до передачи администратору: модель честно не знает ответа."""
    fake.script = [
        AiResult(
            text=_reply_json(
                "Уточню у администратора.", needs_human=True, human_reason="нет в базе знаний"
            )
        )
    ]
    body = await _simulate(admin_client, ctx, "А рассрочка у вас есть?", phone)
    assert body["outcome"] == "escalated", body
    return body


# ── 1. Дата в каждом ходе ───────────────────────────────────────────────────


async def test_every_turn_tells_the_model_current_date(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """Модель видит «СЕЙЧАС» с сегодняшней датой клиники — год больше не из памяти."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Добрый день! Чем помочь?"))]

    await _simulate(admin_client, ctx, "Здравствуйте", _patient())

    last_part = fake.calls[0]["parts"][-1].text
    today = datetime.now(_BISHKEK).date()
    assert last_part.startswith("СЕЙЧАС: ")
    assert f"Сегодня — {today.isoformat()}" in last_part
    assert last_part.endswith("Здравствуйте")
    # Строка в реплике, а не в кэшируемом системном промпте.
    assert "СЕЙЧАС:" not in fake.calls[0]["system"]


# ── 2. Повтор вместо мгновенной передачи ────────────────────────────────────


async def test_fixable_block_gets_one_retry_with_hint(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Время не из слотов → одна перегенерация с подсказкой → ответ уходит пациенту."""
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    fake.script = [
        AiResult(text=_reply_json("Приходите сегодня в 17:35.", facts=["slot:x"])),
        AiResult(text=_reply_json("Подскажите, на какой день вам удобно?")),
    ]

    body = await _simulate(admin_client, ctx, "Хочу записаться", phone)

    assert body["outcome"] == "sent"
    assert len(fake.calls) == 2
    hint = fake.calls[1]["parts"][-1].text
    assert "не из слотов" in hint and "17:35" in hint
    conversation = await _conversation(_resources, phone)
    (message,) = await _bot_messages(_resources, conversation.id)
    assert message.body == "Подскажите, на какой день вам удобно?"
    assert message.validator == {
        "passed": True,
        "codes": [],
        "notes": "после повтора: V02",
        # Выдумка первой попытки остаётся фактической ошибкой для лестницы доверия.
        "retriedCodes": ["V02"],
    }
    assert conversation.status is AiConversationStatus.ACTIVE


async def test_first_attempt_invention_is_kept_when_retry_hands_over(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Выдумал цену, а на подсказку сказал «не знаю» — выдумка всё равно в журнале.

    Лестница доверия считает фактические ошибки за неделю; без этого кода
    модель, выдумывающая цены, выглядела бы безупречной.
    """
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    fake.script = [
        AiResult(text=_reply_json("Чистка стоит 9 900 сом.", facts=["service:x:price"])),
        AiResult(text=_reply_json("Уточню у администратора.", needs_human=True)),
    ]

    body = await _simulate(admin_client, ctx, "Почём чистка?", phone)

    assert body["outcome"] == "escalated"
    conversation = await _conversation(_resources, phone)
    (message,) = await _bot_messages(_resources, conversation.id)
    assert message.validator is not None
    assert message.validator["codes"] == ["V06"]
    assert message.validator["retriedCodes"] == ["V01"]


async def test_second_block_still_goes_to_administrator(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """Повтор не помог — передача администратору, как и раньше."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(text=_reply_json("Приходите в 17:35.", facts=["slot:x"])),
        AiResult(text=_reply_json("Тогда в 18:10.", facts=["slot:x"])),
    ]

    body = await _simulate(admin_client, ctx, "Хочу записаться", _patient())

    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "validator_blocked"
    assert len(fake.calls) == 2


async def test_forbidden_promise_is_not_retried(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """V04 («не больно») — не опечатка: сразу администратору, без второй попытки."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Это совсем не больно, приходите!"))]

    body = await _simulate(admin_client, ctx, "Удалять больно?", _patient())

    assert body["outcome"] == "escalated"
    assert len(fake.calls) == 1


# ── 3. Цифры в названии своей клиники ───────────────────────────────────────


async def test_greeting_with_digits_in_clinic_name_reaches_patient(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Инцидент 12.10: «Клиника «123456»» в приветствии уводила к администратору."""
    async with _resources.factory() as session:
        await session.execute(
            update(Clinic).where(Clinic.id == uuid.UUID(ctx.clinic_id)).values(name="123456")
        )
        await session.commit()
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(
            text=_reply_json(
                "Здравствуйте! Я ассистент стоматологической клиники «123456». Чем помочь?"
            )
        )
    ]

    body = await _simulate(admin_client, ctx, "Здравствуйте", _patient())

    assert body["outcome"] == "sent"
    assert len(fake.calls) == 1  # прошло с первого раза, без повтора


async def test_greeting_with_doctor_in_clinic_name_reaches_patient(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Инцидент 12.10 (вечер): «Доктор Айбек» в названии считался врачом не из справочника."""
    async with _resources.factory() as session:
        await session.execute(
            update(Clinic).where(Clinic.id == uuid.UUID(ctx.clinic_id)).values(name="Доктор Айбек")
        )
        await session.commit()
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(
            text=_reply_json(
                "Здравствуйте! Я ассистент стоматологической клиники «Доктор Айбек». "
                "Чем я могу вам помочь?"
            )
        )
    ]

    body = await _simulate(admin_client, ctx, "Здравствуйте", _patient())

    assert body["outcome"] == "sent"
    assert len(fake.calls) == 1


async def test_unparseable_retry_keeps_the_real_block_reason(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Повтор прислал не JSON — в журнале блок первой попытки, а не «непарсибельный».

    12.10 первый ответ был верным, его снял ложный V03, повтор не разобрался — и
    в ``ai_escalation`` лежало «непарсибельный ответ» с текстом ПЕРВОЙ попытки.
    """
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    fake.script = [
        AiResult(text=_reply_json("Вас примет доктор Небывалов.")),
        AiResult(text="Извините, не могу ответить в формате JSON."),
    ]

    body = await _simulate(admin_client, ctx, "Кто меня примет?", phone)

    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "validator_blocked"
    conversation = await _conversation(_resources, phone)
    async with _resources.factory() as session:
        escalation = (
            await session.execute(
                select(AiEscalation).where(AiEscalation.conversation_id == conversation.id)
            )
        ).scalar_one()
    assert escalation.payload is not None
    assert escalation.payload["codes"] == ["V03"]
    assert escalation.payload["proposed"] == "Вас примет доктор Небывалов."
    (message,) = await _bot_messages(_resources, conversation.id)
    assert message.sent_at is None  # заблокированный ответ пациенту не ушёл


# ── 3a. Сигналы сокета: открытый чат видит передачу и шаблоны ───────────────

_Signals = list[tuple[str, dict[str, Any]]]


@pytest.fixture
def signals(monkeypatch: pytest.MonkeyPatch) -> _Signals:
    """Сигналы, которые конвейер ответа отправил в шину (вместо pg_notify)."""
    recorded: _Signals = []

    async def _record(_session: Any, _clinic_id: Any, type_: str, payload: dict[str, Any]) -> None:
        recorded.append((type_, payload))

    monkeypatch.setattr(reply_service, "publish_realtime", _record)
    return recorded


async def test_escalation_signals_status_and_notice_to_open_screens(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    signals: _Signals,
) -> None:
    """Передача человеку: бейдж «у администратора» и «Уточню…» — без обновления экрана."""
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()

    await _escalate(admin_client, ctx, fake, phone)

    conversation_id = str((await _conversation(_resources, phone)).id)
    assert signals == [
        ("conversation.status", {"conversationId": conversation_id, "status": "escalated"}),
        ("conversation.message", {"conversationId": conversation_id, "direction": "out"}),
    ]


async def test_urgent_signals_template_and_status(
    admin_client: AsyncClient, ctx: Ctx, _resources: Resources, signals: _Signals
) -> None:
    """Неотложное: шаблон с телефоном клиники и смена статуса видны сразу."""
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()

    body = await _simulate(admin_client, ctx, "Щека отекла и температура", phone)

    assert body["outcome"] == "escalated"
    conversation_id = str((await _conversation(_resources, phone)).id)
    assert signals == [
        ("conversation.message", {"conversationId": conversation_id, "direction": "out"}),
        ("conversation.status", {"conversationId": conversation_id, "status": "escalated"}),
    ]


async def test_complaint_template_signals_message_only(
    admin_client: AsyncClient, ctx: Ctx, _resources: Resources, signals: _Signals
) -> None:
    """Жалоба: шаблон ушёл пациенту, диалог остаётся у бота — статус не трогаем."""
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()

    body = await _simulate(admin_client, ctx, "У меня болит зуб уже неделю", phone)

    assert body["outcome"] == "sent"
    conversation_id = str((await _conversation(_resources, phone)).id)
    assert signals == [
        ("conversation.message", {"conversationId": conversation_id, "direction": "out"}),
    ]


async def test_shadow_mode_escalation_signals_status_without_notice(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    signals: _Signals,
) -> None:
    """Теневой режим: пациенту ничего не пишем, но статус на экране меняется."""
    await _set_mode(admin_client, ctx, "shadow")
    phone = _patient()

    await _escalate(admin_client, ctx, fake, phone)

    conversation_id = str((await _conversation(_resources, phone)).id)
    assert signals == [
        ("conversation.status", {"conversationId": conversation_id, "status": "escalated"}),
    ]


async def test_kyrgyz_message_switches_reply_and_templates_language(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Русский разговор, потом «Саламатсызбы» — дальше по-кыргызски, и шаблоны тоже."""
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)

    fake.script = [AiResult(text=_reply_json("Саламатсызбы! Кандай жардам керек?", lang="ky"))]
    switched = await _simulate(admin_client, ctx, "Саламатсызбы", phone)

    assert switched["outcome"] == "sent"
    assert "Отвечай на языке ky" in fake.calls[-1]["system"]
    assert (await _conversation(_resources, phone)).lang is Lang.KG

    # Передача администратору — тоже на языке пациента.
    fake.script = [
        AiResult(text=_reply_json("Тактап берем.", lang="ky", needs_human=True, human_reason="баа"))
    ]
    escalated = await _simulate(admin_client, ctx, "Канча турат рассрочка?", phone)
    assert escalated["outcome"] == "escalated"
    # Вне рабочих часов к шаблону добавляется, когда клиника откроется.
    assert escalated["messages"][-1]["body"].startswith("Администратордон тактап, жооп берем.")


async def test_unrecognised_message_keeps_dialog_language(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Имя без признаков языка («Айбек») не переключает кыргызский диалог на русский."""
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    fake.script = [AiResult(text=_reply_json("Саламатсызбы!", lang="ky"))]
    await _simulate(admin_client, ctx, "Саламатсызбы", phone)

    fake.script = [AiResult(text=_reply_json("Рахмат, Айбек!", lang="ky"))]
    await _simulate(admin_client, ctx, "Айбек", phone)

    assert "Отвечай на языке ky" in fake.calls[-1]["system"]
    assert (await _conversation(_resources, phone)).lang is Lang.KG


# ── 5. Сильная зубная боль ──────────────────────────────────────────────────


async def test_severe_pain_offers_nearest_time_and_alerts_admin(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """«Сильно болит зуб»: время и уведомление администратору, бот не замолкает."""
    await _around_the_clock(client, ctx)
    await own_membership_id(client, ctx.owner_headers)  # в клинике есть врач
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()

    body = await _simulate(
        admin_client, ctx, "На консультацию хотел\nУ меня сильно болит зуб", phone
    )

    assert body["outcome"] == "sent"
    assert fake.calls == []  # шаблон, а не модель
    reply = body["messages"][-1]["body"]
    assert reply.startswith("Сочувствую, с сильной болью лучше не тянуть.")
    assert re.search(r"(сегодня|завтра) в \d{2}:\d{2}", reply), reply
    conversation = await _conversation(_resources, phone)
    assert conversation.status is AiConversationStatus.ACTIVE  # диалог у бота
    assert len(await _events(_resources, "ai.urgent_booking", conversation.id)) == 1
    async with _resources.factory() as session:
        escalations = (
            await session.execute(
                select(AiEscalation).where(AiEscalation.conversation_id == conversation.id)
            )
        ).scalars()
        assert list(escalations) == []


async def test_severe_pain_without_free_time_goes_to_administrator(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Свободного времени нет — «бот предложил время» было бы неправдой: передача человеку."""
    await _set_mode(admin_client, ctx, "auto")  # врачей в клинике нет — и времени нет
    phone = _patient()

    body = await _simulate(admin_client, ctx, "Очень болит зуб", phone)

    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "acute_pain"
    assert "передал администратору" in body["messages"][-1]["body"]
    conversation = await _conversation(_resources, phone)
    assert await _events(_resources, "ai.urgent_booking", conversation.id) == []
    assert len(await _events(_resources, "ai.escalated", conversation.id)) == 1


async def test_severe_pain_in_shadow_mode_goes_to_administrator(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """В теневом режиме пациенту ничего не ушло — администратору честная передача."""
    await _around_the_clock(client, ctx)
    await own_membership_id(client, ctx.owner_headers)
    await _set_mode(admin_client, ctx, "shadow")
    phone = _patient()

    body = await _simulate(admin_client, ctx, "Сильно болит зуб", phone)

    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "acute_pain"
    conversation = await _conversation(_resources, phone)
    assert await _events(_resources, "ai.urgent_booking", conversation.id) == []


async def test_complaint_template_now_shows_time(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """Шаблон жалобы раньше никогда не показывал время — поиск без врача был пуст."""
    await _around_the_clock(client, ctx)
    await own_membership_id(client, ctx.owner_headers)
    await _set_mode(admin_client, ctx, "auto")

    body = await _simulate(admin_client, ctx, "У меня болит зуб", _patient())

    assert body["outcome"] == "sent"
    reply = body["messages"][-1]["body"]
    assert re.search(r"Ближайшее время: (сегодня|завтра) в \d{2}:\d{2}", reply), reply


# ── 6. Прошлое в инструментах ───────────────────────────────────────────────


async def test_slots_for_a_past_date_return_error_with_today(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Инцидент 12.10: модель просила слоты на 2024 год — теперь ошибка и сегодняшняя дата."""
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(
                name="get_available_slots", args={"date": "2024-10-12", "doctorId": doctor_id}
            )
        ),
        AiResult(text=_reply_json("Подскажите, на какой день вам удобно?")),
    ]

    await _simulate(admin_client, ctx, "Запишите на сегодня", _patient())

    async with _resources.factory() as session:
        call = (
            await session.execute(
                select(AiToolCall).where(AiToolCall.tool == "get_available_slots")
            )
        ).scalar_one()
    result = call.result
    assert isinstance(result, dict)
    assert "уже прошла" in result["error"]
    assert result["today"] == datetime.now(_BISHKEK).date().isoformat()


async def test_today_slots_skip_past_hours_and_carry_time(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Слоты на сегодня — только позже текущего момента и с готовым полем time."""
    await _around_the_clock(client, ctx)
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    await _set_mode(admin_client, ctx, "auto")
    today = datetime.now(_BISHKEK).date().isoformat()
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(
                name="get_available_slots", args={"date": today, "doctorId": doctor_id}
            )
        ),
        AiResult(text=_reply_json("Подскажите, какое время удобно?")),
    ]

    await _simulate(admin_client, ctx, "Запишите на сегодня", _patient())

    async with _resources.factory() as session:
        call = (
            await session.execute(
                select(AiToolCall).where(AiToolCall.tool == "get_available_slots")
            )
        ).scalar_one()
    now = datetime.now(UTC)
    result = call.result
    assert isinstance(result, dict)
    if datetime.now(_BISHKEK).hour < 22:
        # Клиника круглосуточная: до 22:00 на сегодня слоты обязаны быть. Позже
        # отступ (30 мин) + округление + длительность (30 мин) упираются в 23:59.
        assert result["slots"], result
    for slot in result["slots"]:
        starts = datetime.fromisoformat(slot["slot"])
        # Не раньше чем через AI_SLOT_LEAD_MINUTES — до ближайшего не успеть.
        assert starts >= now + timedelta(minutes=29)
        assert slot["time"] == starts.strftime("%H:%M")
        # Метка факта — по часам клиники: UTC-время сделало бы сдвиг законным.
        assert slot["fact"].endswith("+06:00")


# ── 7. Напоминание о пациенте, который ждёт ─────────────────────────────────


async def test_patient_writing_to_escalated_dialog_reminds_admin_once(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Инцидент 12.10: два «Здравствуйте» в диалог у администратора ушли в пустоту."""
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    await _escalate(admin_client, ctx, fake, phone)

    first = await _simulate(admin_client, ctx, "Здравствуйте", phone)
    second = await _simulate(admin_client, ctx, "Здравствуйте", phone)

    assert first["outcome"] == second["outcome"] == "skipped"  # бот молчит (§8.4)
    conversation = await _conversation(_resources, phone)
    # Одно напоминание на два сообщения: не чаще раза в 5 минут.
    assert len(await _events(_resources, "conversation.waiting", conversation.id)) == 1


# ── 8. Автовозврат боту через 15 минут ──────────────────────────────────────


async def _backdate_escalations(res: Resources, conversation_id: uuid.UUID, minutes: int) -> None:
    """Сдвинуть передачу (и то, что к ней привело) на ``minutes`` в прошлое.

    Сдвигаются и эскалации, и последнее сообщение пациента — на одну величину:
    порядок событий сохраняется (сообщение → передача → «отвечаю сам»), иначе
    вызвавшее передачу сообщение выглядело бы написанным уже после неё.
    """
    shift = timedelta(minutes=minutes)
    async with res.factory() as session:
        await session.execute(
            update(AiEscalation)
            .where(AiEscalation.conversation_id == conversation_id)
            .values(created_at=AiEscalation.created_at - shift)
        )
        await session.execute(
            update(AiConversation)
            .where(AiConversation.id == conversation_id)
            .values(last_patient_msg_at=AiConversation.last_patient_msg_at - shift)
        )
        await session.commit()


async def _escalated_and_waiting(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    res: Resources,
    *,
    minutes_ago: int,
) -> AiConversation:
    """Пациент ждёт ответа ``minutes_ago`` минут: написал после передачи администратору.

    15 минут автовозврата считаются от этого сообщения (решение 15.10.2026),
    поэтому в прошлое сдвигается и оно, а передача — на минуту раньше него.
    """
    phone = _patient()
    await _escalate(admin_client, ctx, fake, phone)
    conversation = await _conversation(res, phone)
    await _backdate_escalations(res, conversation.id, minutes=minutes_ago + 1)
    waiting = await _simulate(admin_client, ctx, "Здравствуйте", phone)  # пишет в пустоту
    assert waiting["outcome"] == "skipped"
    await _backdate_waiting(res, conversation.id, minutes=minutes_ago)
    return conversation


async def _backdate_waiting(res: Resources, conversation_id: uuid.UUID, minutes: int) -> None:
    """Сдвинуть последнее сообщение пациента на ``minutes`` в прошлое."""
    shift = timedelta(minutes=minutes)
    async with res.factory() as session:
        last_id = (
            await session.execute(
                select(func.max(AiMessage.id)).where(
                    AiMessage.conversation_id == conversation_id,
                    AiMessage.role == AiMessageRole.PATIENT,
                )
            )
        ).scalar_one()
        await session.execute(
            update(AiMessage)
            .where(AiMessage.id == last_id)
            .values(created_at=AiMessage.created_at - shift)
        )
        await session.execute(
            update(AiConversation)
            .where(AiConversation.id == conversation_id)
            .values(last_patient_msg_at=AiConversation.last_patient_msg_at - shift)
        )
        await session.commit()


async def test_dialog_returns_to_bot_after_15_minutes_and_answers_waiting_patient(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Администратор не ответил 15 минут — диалог снова у бота, и бот отвечает."""
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    conversation = await _escalated_and_waiting(admin_client, ctx, fake, _resources, minutes_ago=16)
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем могу помочь?"))]

    returned = await ai_tasks.return_escalations_to_bot({})

    assert returned == 1
    after = await _conversation(_resources, conversation.wa_phone)
    assert after.status is AiConversationStatus.ACTIVE
    async with _resources.factory() as session:
        escalation = (
            await session.execute(
                select(AiEscalation).where(AiEscalation.conversation_id == conversation.id)
            )
        ).scalar_one()
    assert escalation.resolved_at is not None
    assert escalation.resolved_by is None  # закрыла система, не сотрудник
    replies = await _bot_messages(_resources, conversation.id)
    assert replies[-1].body == "Здравствуйте! Чем могу помочь?"
    assert replies[-1].sent_at is not None


async def test_return_counts_15_minutes_from_patient_message_not_handoff(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Передача час назад, пациент написал только что — у администратора ещё 15 минут.

    Решение 15.10.2026: раньше такой диалог через минуту забирал бот, и
    сотрудник не успевал ответить на новое сообщение.
    """
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    await _escalate(admin_client, ctx, fake, phone)
    conversation = await _conversation(_resources, phone)
    await _backdate_escalations(_resources, conversation.id, minutes=60)
    waiting = await _simulate(admin_client, ctx, "Здравствуйте", phone)
    assert waiting["outcome"] == "skipped"

    assert await ai_tasks.return_escalations_to_bot({}) == 0
    assert (await _conversation(_resources, phone)).status is AiConversationStatus.ESCALATED

    await _backdate_waiting(_resources, conversation.id, minutes=16)
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем могу помочь?"))]
    assert await ai_tasks.return_escalations_to_bot({}) == 1


async def test_dialog_without_new_patient_message_is_not_returned(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пациент после передачи молчит — возвращать нечего.

    Иначе бот снова взялся бы за переданное сообщение (фото, сбой отправки) и
    эскалировал бы его по кругу каждые 15 минут с critical-пушем.
    """
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    await _escalate(admin_client, ctx, fake, phone)
    conversation = await _conversation(_resources, phone)
    await _backdate_escalations(_resources, conversation.id, minutes=60)

    assert await ai_tasks.return_escalations_to_bot({}) == 0
    assert await ai_tasks.return_escalations_to_bot({}) == 0  # и на следующем тике
    assert (await _conversation(_resources, phone)).status is AiConversationStatus.ESCALATED
    async with _resources.factory() as session:
        escalations = list(
            (
                await session.execute(
                    select(AiEscalation).where(AiEscalation.conversation_id == conversation.id)
                )
            ).scalars()
        )
    assert len(escalations) == 1  # никаких повторных эскалаций


async def test_fresh_escalation_is_not_returned(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Меньше 15 минут — диалог ещё ждёт администратора, даже если пациент пишет."""
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    conversation = await _escalated_and_waiting(admin_client, ctx, fake, _resources, minutes_ago=10)

    assert await ai_tasks.return_escalations_to_bot({}) == 0
    after = await _conversation(_resources, conversation.wa_phone)
    assert after.status is AiConversationStatus.ESCALATED


async def test_stale_waiting_message_is_not_answered(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пациент писал больше суток назад — окно WhatsApp закрыто, бот не отвечает."""
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    conversation = await _escalated_and_waiting(
        admin_client, ctx, fake, _resources, minutes_ago=3 * 24 * 60
    )
    async with _resources.factory() as session:
        await session.execute(
            update(AiConversation)
            .where(AiConversation.id == conversation.id)
            .values(last_patient_msg_at=datetime.now(UTC) - timedelta(hours=30))
        )
        await session.commit()

    assert await ai_tasks.return_escalations_to_bot({}) == 0


async def test_dialog_where_staff_replied_stays_with_staff(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сотрудник уже ответил пациенту — разговор ведёт человек, бот не вмешивается."""
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    conversation = await _escalated_and_waiting(admin_client, ctx, fake, _resources, minutes_ago=30)
    async with _resources.factory() as session:
        session.add(
            AiMessage(
                conversation_id=conversation.id,
                clinic_id=conversation.clinic_id,
                role=AiMessageRole.ADMIN,
                body="Добрый день, рассрочки нет, но есть оплата частями.",
                sent_at=datetime.now(UTC),
            )
        )
        await session.commit()

    assert await ai_tasks.return_escalations_to_bot({}) == 0
    after = await _conversation(_resources, conversation.wa_phone)
    assert after.status is AiConversationStatus.ESCALATED


@pytest.mark.parametrize(
    "reason", ["manual_takeover", "urgent_medical", "acute_pain", "rate_limited"]
)
async def test_sticky_escalations_are_never_returned(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    """«Отвечаю сам», неотложное, боль без времени, поток — только человек."""
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    conversation = await _escalated_and_waiting(
        admin_client, ctx, fake, _resources, minutes_ago=120
    )
    async with _resources.factory() as session:
        await session.execute(
            update(AiEscalation)
            .where(AiEscalation.conversation_id == conversation.id)
            .values(reason=reason)
        )
        await session.commit()

    assert await ai_tasks.return_escalations_to_bot({}) == 0
    after = await _conversation(_resources, conversation.wa_phone)
    assert after.status is AiConversationStatus.ESCALATED


async def test_take_over_on_escalated_dialog_blocks_auto_return(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Бот передал диалог, сотрудник нажал «отвечаю сам» — бот его не заберёт.

    Раньше перехват в уже переданном диалоге не записывался, и через 15 минут
    автовозврат отдал бы диалог боту посреди разговора человека по телефону.
    """
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    await _escalate(admin_client, ctx, fake, phone)
    conversation = await _conversation(_resources, phone)
    taken = await client.post(
        f"{API}/clinic/conversations/{conversation.id}/assistant",
        headers=ctx.owner_headers,
        json={"active": False},
    )
    assert taken.status_code == 200, taken.text
    await _backdate_escalations(_resources, conversation.id, minutes=60)
    await _simulate(admin_client, ctx, "Алло?", phone)

    assert await ai_tasks.return_escalations_to_bot({}) == 0
    async with _resources.factory() as session:
        reasons = sorted(
            (
                await session.execute(
                    select(AiEscalation.reason).where(
                        AiEscalation.conversation_id == conversation.id
                    )
                )
            ).scalars()
        )
    assert reasons == ["low_confidence", "manual_takeover"]


async def test_orphan_escalation_of_past_episode_does_not_block_return(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Незакрытая эскалация прошлого эпизода не выключает возврат навсегда.

    День 1: передача, администратор ответил, «вернуть боту» никто не нажал;
    диалог закрылся и возобновился. День 10: новая передача — решает она.
    """
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    await _escalate(admin_client, ctx, fake, phone)
    conversation = await _conversation(_resources, phone)
    async with _resources.factory() as session:
        # Эпизод прошлого: эскалация 10 дней назад и ответ сотрудника после неё.
        await session.execute(
            update(AiEscalation)
            .where(AiEscalation.conversation_id == conversation.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=10))
        )
        session.add(
            AiMessage(
                conversation_id=conversation.id,
                clinic_id=conversation.clinic_id,
                role=AiMessageRole.ADMIN,
                body="Ответили вам по телефону.",
                sent_at=datetime.now(UTC) - timedelta(days=9),
                created_at=datetime.now(UTC) - timedelta(days=9),
            )
        )
        # Закрыт и возобновлён новым сообщением: статус active, эскалация открыта.
        await session.execute(
            update(AiConversation)
            .where(AiConversation.id == conversation.id)
            .values(status=AiConversationStatus.ACTIVE)
        )
        await session.commit()
    await _escalate(admin_client, ctx, fake, phone)  # новый эпизод — новая передача
    await _backdate_escalations(_resources, conversation.id, minutes=20)
    await _simulate(admin_client, ctx, "Здравствуйте", phone)
    # 15 минут — от сообщения пациента после передачи (решение 15.10.2026).
    await _backdate_waiting(_resources, conversation.id, minutes=16)
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Слушаю вас."))]

    assert await ai_tasks.return_escalations_to_bot({}) == 1
    assert (await _conversation(_resources, phone)).status is AiConversationStatus.ACTIVE


async def test_new_take_over_is_recorded_despite_open_one_from_past_episode(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Старое «отвечаю сам» прошлого эпизода не гасит новое.

    День 1: перехват, диалог боту так и не вернули. День 10: бот передал диалог
    заново, сотрудник снова жмёт «отвечаю сам» — это должно записаться, иначе
    через 15 минут автовозврат отдаст диалог боту посреди звонка.
    """
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)
    conversation = await _conversation(_resources, phone)

    async def take_over() -> None:
        taken = await client.post(
            f"{API}/clinic/conversations/{conversation.id}/assistant",
            headers=ctx.owner_headers,
            json={"active": False},
        )
        assert taken.status_code == 200, taken.text

    await take_over()  # эпизод прошлого: перехват остался открытым
    async with _resources.factory() as session:
        await session.execute(
            update(AiEscalation)
            .where(AiEscalation.conversation_id == conversation.id)
            .values(created_at=datetime.now(UTC) - timedelta(days=10))
        )
        # Диалог закрылся и возобновился новым сообщением — снова у бота.
        await session.execute(
            update(AiConversation)
            .where(AiConversation.id == conversation.id)
            .values(status=AiConversationStatus.ACTIVE)
        )
        await session.commit()
    await _escalate(admin_client, ctx, fake, phone)
    await take_over()
    await _backdate_escalations(_resources, conversation.id, minutes=30)
    await _simulate(admin_client, ctx, "Алло?", phone)

    assert await ai_tasks.return_escalations_to_bot({}) == 0
    async with _resources.factory() as session:
        reasons = list(
            (
                await session.execute(
                    select(AiEscalation.reason)
                    .where(AiEscalation.conversation_id == conversation.id)
                    .order_by(AiEscalation.created_at)
                )
            ).scalars()
        )
    assert reasons == ["manual_takeover", "low_confidence", "manual_takeover"]
