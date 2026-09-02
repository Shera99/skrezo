"""API-тесты каркаса AI-ассистента (Ф2, инкремент 1).

Модель подменяется программируемым фейком (реальный Gemini в тестах не
зовётся) — проверяется НАША логика, где живут ошибки:

* конвейер: симулятор → настоящий разбор входящего → ответ бота;
* режимы: shadow не пишет пациенту (sent=false), auto — пишет (лог-канал);
* инструменты: вызов журналируется в ai_tool_call, результат уходит модели;
* валидатор: запрещённая фраза («не больно») блокирует ответ и эскалирует;
* эскалация ставит бота на паузу — следующее сообщение он молчит;
* глоссарий: предложение бота попадает в pending, после approve — в промпт.
"""

from __future__ import annotations

import itertools
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, update

from app.main import app
from app.modules.ai.presentation.deps import get_ai_provider
from app.modules.whatsapp.domain.window import WINDOW
from app.shared.ai import (
    AiMessagePart,
    AiProviderError,
    AiResult,
    AiToolDecl,
    ToolCallRequest,
)
from app.shared.db.enums import AiConversationStatus, AiMessageRole, AiMode
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_summary import AiSummary
from app.shared.db.models.ai_tool_call import AiToolCall
from app.shared.db.models.appointment import Appointment
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.event import Event
from app.shared.db.models.patient import Patient
from app.shared.db.models.slot_hold import SlotHold
from tests.conftest import API, Resources, otp_login, own_membership_id

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070080{next(_phone_seq):04d}"


def _reply_json(
    reply: str,
    *,
    lang: str = "ru",
    intent: str = "info",
    facts: list[str] | None = None,
    confidence: str = "high",
    needs_human: bool = False,
) -> str:
    return json.dumps(
        {
            "reply": reply,
            "lang": lang,
            "intent": intent,
            "used_facts": facts or [],
            "confidence": confidence,
            "needs_human": needs_human,
            "human_reason": None,
        },
        ensure_ascii=False,
    )


@dataclass
class _FakeProvider:
    """Отдаёт заранее заданный сценарий ходов; записывает, что получил."""

    script: list[AiResult]
    model_name: str = "fake-model"
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self,
        *,
        system: str,
        parts: list[AiMessagePart],
        tools: list[AiToolDecl],
        temperature: float,
        max_tokens: int,
    ) -> AiResult:
        self.calls.append({"system": system, "parts": parts})
        if not self.script:
            return AiResult(text=_reply_json("Хорошо."), model=self.model_name)
        return self.script.pop(0)


@dataclass(slots=True)
class Ctx:
    clinic_id: str
    owner_headers: dict[str, str]
    phone_number_id: str


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[_FakeProvider]:
    provider = _FakeProvider(script=[])
    app.dependency_overrides[get_ai_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(get_ai_provider, None)


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> Ctx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец ИИ",
            "adminPhone": phone,
            "name": "Клиника ИИ",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    clinic_id = str(created.json()["id"])
    phone_number_id = f"9000{next(_phone_seq):06d}"
    bound = await admin_client.put(
        f"{API}/admin/clients/{clinic_id}/whatsapp",
        json={"phoneNumberId": phone_number_id, "displayPhone": "+996700999222"},
    )
    assert bound.status_code == 200, bound.text
    headers = await otp_login(client, phone)
    service = await client.post(
        f"{API}/clinic/services",
        headers=headers,
        json={"name": "Чистка", "priceTiyin": 250000},
    )
    assert service.status_code == 201, service.text
    return Ctx(clinic_id=clinic_id, owner_headers=headers, phone_number_id=phone_number_id)


async def _set_mode(admin_client: AsyncClient, ctx: Ctx, mode: str) -> None:
    resp = await admin_client.put(
        f"{API}/admin/clients/{ctx.clinic_id}/ai-mode", json={"mode": mode}
    )
    assert resp.status_code == 200, resp.text


async def _simulate(admin_client: AsyncClient, ctx: Ctx, text: str, phone: str) -> Any:
    resp = await admin_client.post(
        f"{API}/admin/ai/simulate-inbound",
        json={"clinicId": ctx.clinic_id, "fromPhone": phone, "text": text},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1. Режимы ───────────────────────────────────────────────────────────────


async def test_shadow_keeps_reply_from_patient(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """shadow: бот готовит ответ, но пациенту он НЕ уходит (sent=false)."""
    await _set_mode(admin_client, ctx, "shadow")
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?"), model="fake")]
    body = await _simulate(admin_client, ctx, "Салам!", "+996700200001")
    assert body["outcome"] == "shadow"
    assert body["botReply"] == "Здравствуйте! Чем помочь?"
    bot = next(m for m in body["messages"] if m["role"] == "bot")
    assert bot["sent"] is False


async def test_auto_sends_reply(admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider) -> None:
    """auto: прошедший валидатор ответ уходит пациенту (лог-канал WhatsApp)."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"), model="fake")]
    body = await _simulate(admin_client, ctx, "Привет", "+996700200002")
    assert body["outcome"] == "sent"
    bot = next(m for m in body["messages"] if m["role"] == "bot")
    assert bot["sent"] is True


async def test_off_mode_skips_assistant(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """off: сообщение сохраняется, бот не зовётся вовсе — как до Ф2."""
    body = await _simulate(admin_client, ctx, "Есть кто?", "+996700200003")
    assert body["outcome"] == "skipped"
    assert fake.calls == []


# ── 2. Инструменты и факты ──────────────────────────────────────────────────


async def test_tools_are_executed_and_journaled(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Вызов инструмента исполняется, журналируется и его результат видит модель."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="get_services", args={"query": "чистка"})),
        AiResult(
            text=_reply_json(
                "Чистка стоит 2 500 сом.",
                intent="info",
                facts=["service:x:price:2 500"],
            )
        ),
    ]
    body = await _simulate(admin_client, ctx, "Канча турат чистка?", "+996700200004")
    assert body["outcome"] == "sent"
    assert "2 500" in body["botReply"]

    # Модель получила результат инструмента вторым вызовом.
    second_call_parts = fake.calls[1]["parts"]
    tool_parts = [p for p in second_call_parts if p.role == "tool"]
    assert tool_parts and "2 500" in json.dumps(tool_parts[-1].tool_result, ensure_ascii=False)

    async with _resources.factory() as session:
        journal = list((await session.execute(select(AiToolCall))).scalars())
    assert [j.tool for j in journal] == ["get_services"]
    assert journal[0].error is None


# ── 3. Валидатор и эскалации ────────────────────────────────────────────────


async def test_forbidden_phrase_blocks_and_escalates(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """«Не больно» — блок валидатора: пациенту шаблон, админу эскалация."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Это совсем не больно, приходите!"))]
    body = await _simulate(admin_client, ctx, "Больно ли удалять?", "+996700200005")
    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "validator_blocked"
    # Заблокированный ответ в журнале не отправлен; пациенту ушёл шаблон.
    roles = {(m["role"], m["sent"]) for m in body["messages"]}
    assert ("bot", False) in roles
    assert ("system", True) in roles

    async with _resources.factory() as session:
        events = list(
            (await session.execute(select(Event).where(Event.type == "ai.escalated"))).scalars()
        )
    assert len(events) == 1  # админа позовёт диспетчер Ф3


async def test_escalation_pauses_bot(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """Пока диалог у человека (escalated) — бот молчит (§8.4)."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Гарантирую результат!"))]  # блок → эскалация
    await _simulate(admin_client, ctx, "Поможете?", "+996700200006")
    calls_before = len(fake.calls)
    body = await _simulate(admin_client, ctx, "Ау, вы тут?", "+996700200006")
    assert body["outcome"] == "skipped"
    assert len(fake.calls) == calls_before  # модель больше не звали


async def test_needs_human_escalates_without_block(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """«Не знаю» — правильный ответ: needs_human → эскалация low_confidence."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Уточню у администратора.", needs_human=True))]
    body = await _simulate(admin_client, ctx, "А рассрочка есть?", "+996700200007")
    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "low_confidence"


# ── 4. Глоссарий с очередью ─────────────────────────────────────────────────


async def test_glossary_suggestion_flow(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """Бот предлагает термин → pending (в промпт не попадает) → approve → в промпте."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(
                name="suggest_glossary_term",
                args={"phrase": "тиш чыгаруу", "meaning": "удаление зуба", "lang": "ky"},
            )
        ),
        AiResult(text=_reply_json("Записал! Речь про удаление зуба.", intent="clarify")),
    ]
    await _simulate(admin_client, ctx, "Ооба, тиш чыгаруу деп жаттым", "+996700200008")

    pending = await client.get(f"{API}/clinic/glossary?status=pending", headers=ctx.owner_headers)
    assert pending.status_code == 200, pending.text
    terms = pending.json()["list"]
    assert len(terms) == 1
    assert terms[0]["phrase"] == "тиш чыгаруу"
    assert terms[0]["source"] == "assistant"

    # До одобрения фразы в системном промпте нет.
    assert "тиш чыгаруу" not in fake.calls[-1]["system"]

    approved = await client.post(
        f"{API}/clinic/glossary/{terms[0]['id']}/approve", headers=ctx.owner_headers
    )
    assert approved.status_code == 200, approved.text

    fake.script = [AiResult(text=_reply_json("Удаление зуба — от 1 500 сом? Уточню!"))]
    await _simulate(admin_client, ctx, "Тиш чыгаруу канча?", "+996700200008")
    assert "тиш чыгаруу" in fake.calls[-1]["system"]  # термин теперь в промпте


async def test_manual_term_and_delete(
    client: AsyncClient, admin_client: AsyncClient, ctx: Ctx
) -> None:
    """Ручной термин действует сразу; удаление работает; чужие/общие — нельзя."""
    created = await client.post(
        f"{API}/clinic/glossary",
        headers=ctx.owner_headers,
        json={"phrase": "чистка", "meaning": "профгигиена", "lang": "ru"},
    )
    assert created.status_code == 201, created.text
    term_id = created.json()["id"]
    listed = await client.get(f"{API}/clinic/glossary", headers=ctx.owner_headers)
    assert any(t["id"] == term_id and t["status"] == "approved" for t in listed.json()["list"])
    deleted = await client.delete(f"{API}/clinic/glossary/{term_id}", headers=ctx.owner_headers)
    assert deleted.status_code == 204


# ── 5. Запись на приём: интервью → резерв → кнопка ──────────────────────────


def _workday_ten() -> str:
    """10:00 ближайшего рабочего дня (воскресенье — выходной по умолчанию)."""
    day = datetime.now(UTC).date() + timedelta(days=1)
    if day.weekday() == 6:
        day += timedelta(days=1)
    return f"{day.isoformat()}T10:00:00+06:00"


async def _service_id(client: AsyncClient, ctx: Ctx) -> str:
    resp = await client.get(f"{API}/clinic/services", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    return str(resp.json()[0]["id"])


def _tool_result(parts: list[AiMessagePart], tool: str) -> Any:
    """Результат инструмента, каким его увидела модель следующим ходом."""
    part = next(p for p in parts if p.role == "tool" and p.tool_name == tool)
    return part.tool_result


async def _press_button(admin_client: AsyncClient, ctx: Ctx, phone: str, payload: str) -> Any:
    resp = await admin_client.post(
        f"{API}/admin/ai/simulate-inbound",
        json={
            "clinicId": ctx.clinic_id,
            "fromPhone": phone,
            "text": "-",
            "buttonPayload": payload,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_booking_button_creates_appointment_once(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Полный цикл: карточка пациента → резерв → кнопка → приём; повтор кнопки идемпотентен."""
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    service_id = await _service_id(client, ctx)
    slot = _workday_ten()
    phone = "+996700200010"

    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айбек"})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={"doctorId": doctor_id, "startsAt": slot, "serviceId": service_id},
            )
        ),
        AiResult(text=_reply_json("Отправил кнопку подтверждения — нажмите её.", intent="booking")),
    ]
    body = await _simulate(admin_client, ctx, "Запишите меня, я Айбек. Завтра в 10", phone)
    assert body["outcome"] == "sent"

    # Резерв создан, кнопка ушла (auto), приёма ещё НЕТ.
    async with _resources.factory() as session:
        hold = (await session.execute(select(SlotHold))).scalar_one()
        assert hold.consumed_at is None and hold.appointment_id is None
        assert (await session.execute(select(Appointment))).scalar_one_or_none() is None
        confirmation_id = f"hold:{hold.id}"
    propose_result = _tool_result(fake.calls[2]["parts"], "propose_appointment")
    assert propose_result["confirmationId"] == confirmation_id
    assert propose_result["buttonSent"] is True

    # Нажатие кнопки: приём создаётся штатным use-case, пациенту — «вы записаны».
    pressed = await _press_button(admin_client, ctx, phone, confirmation_id)
    assert pressed["outcome"] == "button"
    assert any("вы записаны" in (m["body"] or "") for m in pressed["messages"])
    async with _resources.factory() as session:
        appointment = (await session.execute(select(Appointment))).scalar_one()
        assert appointment.comment == "Запись через WhatsApp-ассистента"
        assert appointment.price_total_tiyin == 250000  # услуга легла позицией
        hold = (await session.execute(select(SlotHold))).scalar_one()
        assert hold.appointment_id == appointment.id and hold.consumed_at is not None

    # Повторное нажатие — второй приём не создаётся.
    again = await _press_button(admin_client, ctx, phone, confirmation_id)
    assert any("уже записаны" in (m["body"] or "") for m in again["messages"])
    async with _resources.factory() as session:
        count = len(list((await session.execute(select(Appointment))).scalars()))
    assert count == 1
    # Кнопку модель не видела: её ходы кончились на трёх записанных.
    assert len(fake.calls) == 3


async def test_booking_expired_hold_declines(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Протухший резерв: вежливый отказ, приём не создаётся."""
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    phone = "+996700200011"
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Гүлнара"})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={"doctorId": doctor_id, "startsAt": _workday_ten()},
            )
        ),
        AiResult(text=_reply_json("Отправил кнопку подтверждения.", intent="booking")),
    ]
    await _simulate(admin_client, ctx, "Я Гүлнара, запишите на завтра", phone)
    async with _resources.factory() as session:
        hold = (await session.execute(select(SlotHold))).scalar_one()
        hold.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()
        confirmation_id = f"hold:{hold.id}"

    pressed = await _press_button(admin_client, ctx, phone, confirmation_id)
    assert any("больше не действует" in (m["body"] or "") for m in pressed["messages"])
    async with _resources.factory() as session:
        assert (await session.execute(select(Appointment))).scalar_one_or_none() is None


async def test_propose_without_patient_is_tool_error(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Резерв без карточки пациента — ошибка данными в модель, не 500."""
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={"doctorId": doctor_id, "startsAt": _workday_ten()},
            )
        ),
        AiResult(text=_reply_json("Как вас зовут? Запишу карточку и подтвержу время.")),
    ]
    body = await _simulate(admin_client, ctx, "Запишите завтра на 10", "+996700200012")
    assert body["outcome"] == "sent"
    tool_result = _tool_result(fake.calls[1]["parts"], "propose_appointment")
    assert "имя" in json.dumps(tool_result, ensure_ascii=False)
    async with _resources.factory() as session:
        assert (await session.execute(select(SlotHold))).scalar_one_or_none() is None


async def test_available_days_tool_and_interview_prompt(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
) -> None:
    """get_available_days отдаёт ближайшие свободные дни; промпт учит интервью записи."""
    await _set_mode(admin_client, ctx, "shadow")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(
                name="get_available_days", args={"days": 3, "doctorId": doctor_id}
            )
        ),
        AiResult(text=_reply_json("Есть время завтра — когда удобно?")),
    ]
    body = await _simulate(admin_client, ctx, "Когда можно прийти?", "+996700200013")
    assert body["outcome"] == "shadow"

    days_result = _tool_result(fake.calls[1]["parts"], "get_available_days")
    assert days_result["days"], days_result
    first = days_result["days"][0]
    assert first["freeSlots"] >= 1 and first["firstSlot"]

    system = fake.calls[0]["system"]
    assert "КАК ВЕСТИ ЗАПИСЬ" in system
    # Медицинская граница: по симптомам — только осмотр/консультация.
    assert "осмотр" in system.lower()


async def test_booking_slot_race_apologizes(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Слот заняли между кнопкой и резервом: извинение, резерв погашен, не 500.

    Ветка с rollback внутри confirm — регресс MissingGreenlet: после отката
    ORM-объект диалога протухает, и ответ пациенту падал 500-й.
    """
    await _set_mode(admin_client, ctx, "auto")
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    service_id = await _service_id(client, ctx)
    slot = _workday_ten()
    phone = "+996700200014"
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Нурлан"})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={"doctorId": doctor_id, "startsAt": slot, "serviceId": service_id},
            )
        ),
        AiResult(text=_reply_json("Отправил кнопку подтверждения.", intent="booking")),
    ]
    await _simulate(admin_client, ctx, "Я Нурлан, запишите на завтра в 10", phone)
    async with _resources.factory() as session:
        hold = (await session.execute(select(SlotHold))).scalar_one()
        confirmation_id = f"hold:{hold.id}"

    # Соперник занимает то же время штатной записью из приложения клиники.
    rival = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.owner_headers,
        json={"phone": "0700200015", "firstName": "Сопер", "lastName": "Ник"},
    )
    assert rival.status_code == 201, rival.text
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.owner_headers,
        json={
            "patientId": rival.json()["id"],
            "doctorMembershipId": doctor_id,
            "scheduledAt": slot,
        },
    )
    assert booked.status_code == 201, booked.text

    pressed = await _press_button(admin_client, ctx, phone, confirmation_id)
    assert any("только что заняли" in (m["body"] or "") for m in pressed["messages"])
    async with _resources.factory() as session:
        hold = (await session.execute(select(SlotHold))).scalar_one()
        assert hold.consumed_at is not None and hold.appointment_id is None
        count = len(list((await session.execute(select(Appointment))).scalars()))
    assert count == 1  # только запись соперника

    # Повтор кнопки после проигранной гонки — «предложение больше не действует».
    again = await _press_button(admin_client, ctx, phone, confirmation_id)
    assert any("больше не действует" in (m["body"] or "") for m in again["messages"])


async def test_propose_rejects_unknown_doctor(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Выдуманный моделью doctorId отбивается на propose — резерв не создаётся."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Азат"})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment",
                args={"doctorId": str(uuid.uuid4()), "startsAt": _workday_ten()},
            )
        ),
        AiResult(text=_reply_json("Сейчас уточню врача и вернусь.")),
    ]
    body = await _simulate(admin_client, ctx, "Я Азат, запишите завтра", "+996700200016")
    assert body["outcome"] == "sent"
    tool_result = _tool_result(fake.calls[2]["parts"], "propose_appointment")
    assert "get_doctors" in json.dumps(tool_result, ensure_ascii=False)
    async with _resources.factory() as session:
        assert (await session.execute(select(SlotHold))).scalar_one_or_none() is None


# ── 6. Медицинский фильтр (§9) ──────────────────────────────────────────────


async def test_urgent_bypasses_model_and_alerts(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Неотложное: телефон клиники, срочное событие, бот замолкает — без модели."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Этого ответа не должно быть."))]
    body = await _simulate(
        admin_client, ctx, "Опухла щека и температура, что делать?", "+996700200020"
    )

    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "urgent_medical"
    assert fake.calls == []  # §9.2: реакция минуя модель

    patient_reply = body["messages"][-1]
    assert "0700000000" in patient_reply["body"]  # телефон клиники из карточки
    assert patient_reply["sent"] is True

    async with _resources.factory() as session:
        types = [
            e.type
            for e in (await session.execute(select(Event))).scalars()
            if e.type.startswith("ai.")
        ]
    assert "ai.urgent" in types

    # Диалог передан человеку: следующее сообщение бот игнорирует (§8.4).
    again = await _simulate(admin_client, ctx, "Ну так что?", "+996700200020")
    assert again["outcome"] == "skipped"


async def test_complaint_answers_with_template_and_slots(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Жалоба: состояние не обсуждаем, зовём к врачу и сразу предлагаем время."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Похоже на пульпит."))]
    body = await _simulate(admin_client, ctx, "У меня болит зуб", "+996700200021")

    assert body["outcome"] == "sent"
    assert fake.calls == []  # §9.1: классификатор ДО модели
    reply = body["messages"][-1]["body"]
    assert "показаться врачу" in reply
    assert "пульпит" not in reply  # никаких догадок о диагнозе

    # Это обычный ответ, а не эскалация: диалог остаётся за ботом.
    async with _resources.factory() as session:
        conv = (await session.execute(select(AiConversation))).scalars().all()
    assert all(c.status is AiConversationStatus.ACTIVE for c in conv)


async def test_service_name_with_diagnosis_is_not_a_complaint(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """«Сколько стоит лечение кариеса» — вопрос о прайсе, а не жалоба.

    Названия болезней есть в прайсе клиники, и фильтр не должен подменять
    ответ о цене шаблоном про врача.
    """
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Такая услуга у нас есть, сейчас уточню."))]
    body = await _simulate(admin_client, ctx, "Сколько стоит лечение кариеса?", "+996700200022")
    assert body["outcome"] == "sent"
    assert len(fake.calls) == 1  # модель отработала штатно, фильтр не вмешался


async def test_procedure_question_is_not_a_complaint(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """«Больно ли удалять?» — вопрос о процедуре: отвечает модель, не фильтр.

    Граница между §9 и §7: жалоба на СВОЁ состояние — к фильтру, вопрос про
    процедуру — к модели, а обещание «не больно» ловит валидатор (V04).
    """
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Удаление делают с анестезией.", facts=[]))]
    body = await _simulate(admin_client, ctx, "Больно ли удалять?", "+996700200024")
    assert body["outcome"] == "sent"
    assert len(fake.calls) == 1


async def test_urgent_without_clinic_phone_still_alerts(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Телефона в карточке нет — пациент всё равно получает ответ, админ — пуш."""
    async with _resources.factory() as session:
        clinic = await session.get(Clinic, uuid.UUID(ctx.clinic_id))
        assert clinic is not None
        clinic.business_phone = None
        await session.commit()
    await _set_mode(admin_client, ctx, "auto")
    body = await _simulate(admin_client, ctx, "Кровотечение не останавливается", "+996700200023")

    assert body["outcome"] == "escalated"
    assert "администратору" in body["messages"][-1]["body"]
    assert fake.calls == []


# ── 7. Валидатор V01–V03: сверка с фактами хода ─────────────────────────────


async def test_invented_price_is_blocked(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """V01: цена, которой не было ни в прайсе, ни в истории — блок и эскалация.

    Главная защита от выдуманной цены: сама модель о своей выдумке не сообщит,
    а `used_facts` она заполняет тоже сама.
    """
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Чистка стоит 9 900 сом.", facts=["service:x:price"]))]
    body = await _simulate(admin_client, ctx, "Почём чистка?", "+996700200030")
    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "validator_blocked"

    async with _resources.factory() as session:
        blocked = (
            (await session.execute(select(AiMessage).where(AiMessage.role == AiMessageRole.BOT)))
            .scalars()
            .all()
        )
    assert any(m.validator and "V01" in (m.validator.get("codes") or []) for m in blocked)


async def test_price_from_tool_passes(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """Та же цена, но взятая из прайса инструментом, проходит."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="get_services", args={"query": "чистка"})),
        AiResult(text=_reply_json("Чистка стоит 2 500 сом.", facts=["service:x:price"])),
    ]
    body = await _simulate(admin_client, ctx, "Почём чистка?", "+996700200031")
    assert body["outcome"] == "sent"
    assert "2 500" in body["botReply"]


async def test_invented_slot_time_is_blocked(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """V02: время, которого не было в выдаче слотов, — блок."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Приходите 05.09 в 17:35.", facts=["slot:x"]))]
    body = await _simulate(admin_client, ctx, "Когда можно прийти?", "+996700200032")
    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "validator_blocked"


async def test_unknown_doctor_is_blocked(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """V03: врач не из справочника клиники — блок (падежи учтены)."""
    await _set_mode(admin_client, ctx, "auto")
    await own_membership_id(client, ctx.owner_headers)  # в клинике есть врач
    fake.script = [AiResult(text=_reply_json("Вас примет врач Петрова."))]
    body = await _simulate(admin_client, ctx, "А кто принимает?", "+996700200033")
    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "validator_blocked"


# ── 8. Лимит потока сообщений (§14) ─────────────────────────────────────────


async def test_message_flood_answers_once_then_hands_over(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Больше 30 сообщений в час: одно извинение, эскалация, дальше молчание.

    Молча игнорировать нельзя — человек решит, что его не слышат. Но и двух
    сообщений подряд («слишком много» + «уточню у администратора») быть не
    должно: §14 требует ровно одно.
    """
    await _set_mode(admin_client, ctx, "auto")
    phone = "+996700200040"
    fake.script = [AiResult(text=_reply_json("Здравствуйте!")) for _ in range(31)]
    for i in range(30):
        await _simulate(admin_client, ctx, f"сообщение {i}", phone)
    calls_before = len(fake.calls)

    body = await _simulate(admin_client, ctx, "тридцать первое", phone)
    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "rate_limited"
    assert len(fake.calls) == calls_before  # модель на перелив не звали

    outgoing = [m for m in body["messages"] if m["role"] != "patient" and m["sent"]]
    assert "Слишком много сообщений" in outgoing[-1]["body"]
    assert "администратора" not in outgoing[-1]["body"]  # ровно одно извинение

    # Диалог у человека — следующее сообщение бот пропускает.
    after = await _simulate(admin_client, ctx, "ещё одно", phone)
    assert after["outcome"] == "skipped"


# ── 9. Резюме закрытого диалога (§3.3) ──────────────────────────────────────


def _patch_job(
    monkeypatch: pytest.MonkeyPatch, module: Any, provider: _FakeProvider, res: Resources
) -> None:
    """Фоновая задача живёт вне приложения: у неё своя сессия и свой провайдер."""
    monkeypatch.setattr(module, "session_factory", res.factory)
    monkeypatch.setattr(module, "build_ai_provider", lambda: provider)
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory", res.factory
    )


async def test_closed_conversation_gets_summary_and_next_talk_sees_it(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Диалог закрылся — появилось резюме; следующий разговор видит его в промпте."""
    from app.jobs.tasks import whatsapp as wa_job
    from app.jobs.tasks.whatsapp import close_stale_conversations

    _patch_job(monkeypatch, wa_job, fake, _resources)

    await _set_mode(admin_client, ctx, "auto")
    phone = "+996700200050"
    fake.script = [AiResult(text=_reply_json("Записал вас на четверг."))]
    await _simulate(admin_client, ctx, "Хочу записаться", phone)

    # Сутки тишины: отматываем время последнего сообщения назад.
    async with _resources.factory() as session:
        conv = (
            await session.execute(select(AiConversation).where(AiConversation.wa_phone == phone))
        ).scalar_one()
        conv.last_msg_at = datetime.now(UTC) - WINDOW - timedelta(minutes=5)
        await session.commit()

    fake.script = [AiResult(text="Пациентка записалась на чистку в четверг.")]
    closed = await close_stale_conversations({})
    assert closed >= 1

    async with _resources.factory() as session:
        summaries = list((await session.execute(select(AiSummary))).scalars())
    assert len(summaries) == 1
    assert "чистку" in summaries[0].body
    assert summaries[0].wa_phone == phone

    # Новый разговор с того же номера: резюме уехало в системный промпт.
    fake.script = [AiResult(text=_reply_json("Здравствуйте снова!"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)
    system = fake.calls[-1]["system"]
    assert "О ЧЁМ ГОВОРИЛИ РАНЬШЕ" in system
    assert "записалась на чистку" in system


async def test_summary_is_built_once(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повторный прогон задачи не плодит резюме одного диалога."""
    from app.jobs.tasks import whatsapp as wa_job
    from app.jobs.tasks.whatsapp import close_stale_conversations

    _patch_job(monkeypatch, wa_job, fake, _resources)

    await _set_mode(admin_client, ctx, "auto")
    phone = "+996700200051"
    fake.script = [AiResult(text=_reply_json("Хорошо."))]
    await _simulate(admin_client, ctx, "Добрый день", phone)
    async with _resources.factory() as session:
        conv = (
            await session.execute(select(AiConversation).where(AiConversation.wa_phone == phone))
        ).scalar_one()
        conv.last_msg_at = datetime.now(UTC) - WINDOW - timedelta(minutes=5)
        await session.commit()

    fake.script = [AiResult(text="Пациент поздоровался, вопросов не было.")]
    await close_stale_conversations({})
    # Второй прогон: диалог уже closed, но подстрахуемся и от прямого вызова.
    async with _resources.factory() as session:
        from app.modules.ai.application.summary import ConversationSummaryService

        again = await ConversationSummaryService(session, fake).build(conv.id)
        assert again is None

    async with _resources.factory() as session:
        count = len(list((await session.execute(select(AiSummary))).scalars()))
    assert count == 1


# ── 10. Голосовые и нечитаемые сообщения (§11) ──────────────────────────────


async def _voice(
    res: Resources,
    conversation_id: uuid.UUID,
    clinic_id: str,
    *,
    transcript: str | None,
    confidence: float | None,
    media_type: str = "audio",
) -> None:
    """Положить в диалог входящее медиа с заданной расшифровкой."""
    async with res.factory() as session:
        session.add(
            AiMessage(
                conversation_id=conversation_id,
                clinic_id=uuid.UUID(clinic_id),
                role=AiMessageRole.PATIENT,
                body="",
                wa_message_id=f"wamid.V{uuid.uuid4().hex}",
                media_type=media_type,
                media_id="media-1",
                transcript=transcript,
                transcript_confidence=confidence,
                sent_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _run_reply(res: Resources, fake: _FakeProvider, conversation_id: uuid.UUID) -> Any:
    from app.modules.ai.application.reply_service import ProcessAiReplyService
    from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient

    async with res.factory() as session:
        return await ProcessAiReplyService(
            session, fake, LoggingWhatsAppClient()
        ).execute(conversation_id)


async def _conversation_id(res: Resources, phone: str) -> uuid.UUID:
    async with res.factory() as session:
        conv = (
            await session.execute(select(AiConversation).where(AiConversation.wa_phone == phone))
        ).scalar_one()
        return conv.id


async def test_voice_transcript_reaches_the_model(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Уверенная расшифровка участвует в диалоге как обычный текст."""
    await _set_mode(admin_client, ctx, "auto")
    phone = "+996700200060"
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)
    conv_id = await _conversation_id(_resources, phone)

    await _voice(
        _resources, conv_id, ctx.clinic_id, transcript="Хочу записаться", confidence=0.95
    )
    fake.script = [AiResult(text=_reply_json("Конечно, на какой день?"))]
    outcome = await _run_reply(_resources, fake, conv_id)

    assert outcome.status == "sent"
    last_user = [p for p in fake.calls[-1]["parts"] if p.role == "user"][-1]
    assert last_user.text == "Хочу записаться"


async def test_low_confidence_voice_goes_to_human(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Плохо разобранное голосовое — к человеку, а не догадка (§11)."""
    await _set_mode(admin_client, ctx, "auto")
    phone = "+996700200061"
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)
    conv_id = await _conversation_id(_resources, phone)

    await _voice(
        _resources, conv_id, ctx.clinic_id, transcript="что-то неразборчивое", confidence=0.2
    )
    calls_before = len(fake.calls)
    outcome = await _run_reply(_resources, fake, conv_id)

    assert outcome.status == "escalated"
    assert outcome.escalation_reason == "asr_low_confidence"
    assert len(fake.calls) == calls_before  # модель не звали вовсе


async def test_voice_without_asr_goes_to_human(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Распознавание не настроено — голосовое уходит администратору."""
    await _set_mode(admin_client, ctx, "auto")
    phone = "+996700200062"
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)
    conv_id = await _conversation_id(_resources, phone)

    await _voice(_resources, conv_id, ctx.clinic_id, transcript=None, confidence=None)
    outcome = await _run_reply(_resources, fake, conv_id)
    assert outcome.escalation_reason == "asr_unavailable"


async def test_photo_is_not_interpreted(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Снимок в первой версии не интерпретируем: сохраняем и зовём человека."""
    await _set_mode(admin_client, ctx, "auto")
    phone = "+996700200063"
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)
    conv_id = await _conversation_id(_resources, phone)

    await _voice(
        _resources,
        conv_id,
        ctx.clinic_id,
        transcript=None,
        confidence=None,
        media_type="image",
    )
    outcome = await _run_reply(_resources, fake, conv_id)
    assert outcome.escalation_reason == "unsupported_media"


# ── 11. Мониторинг здоровья интеграции (§13.5) ──────────────────────────────


@dataclass
class _DeadProvider:
    """Провайдер, который лёг: любой вызов — ошибка."""

    model_name: str = "dead-model"

    async def complete(self, **_: Any) -> AiResult:
        raise AiProviderError("провайдер недоступен")


async def _health(res: Resources, provider: Any) -> Any:
    from app.modules.ai.application.health import AiHealthService
    from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient

    async with res.factory() as session:
        return await AiHealthService(session, provider, LoggingWhatsAppClient()).check()


async def test_dead_model_downgrades_auto_clinics(
    admin_client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Модель недоступна: клиника в auto опускается до assisted, алерт — нам.

    §13.5, правило 1: клиника не может починить наш провайдер, ей достаётся
    деградация поведения — ответы уходят через администратора.
    """
    await _set_mode(admin_client, ctx, "auto")
    report = await _health(_resources, _DeadProvider())

    assert report.model_ok is False
    assert report.downgraded >= 1
    async with _resources.factory() as session:
        clinic = await session.get(Clinic, uuid.UUID(ctx.clinic_id))
        assert clinic is not None
        assert clinic.ai_mode is AiMode.ASSISTED


async def test_healthy_model_changes_nothing(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Живая модель — режим клиники не трогаем."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text="ок")]
    report = await _health(_resources, fake)

    assert report.model_ok is True
    assert report.downgraded == 0
    async with _resources.factory() as session:
        clinic = await session.get(Clinic, uuid.UUID(ctx.clinic_id))
        assert clinic is not None
        assert clinic.ai_mode is AiMode.AUTO


async def test_silence_of_busy_clinic_is_flagged(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Клиника, писавшая всю неделю и вдруг замолчавшая, — подозрение на отказ."""
    await _set_mode(admin_client, ctx, "shadow")
    phone = "+996700200070"
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)
    conv_id = await _conversation_id(_resources, phone)

    stale = datetime.now(UTC) - timedelta(hours=20)
    async with _resources.factory() as session:
        for i in range(12):
            session.add(
                AiMessage(
                    conversation_id=conv_id,
                    clinic_id=uuid.UUID(ctx.clinic_id),
                    role=AiMessageRole.PATIENT,
                    body=f"старое {i}",
                    wa_message_id=f"wamid.S{uuid.uuid4().hex}",
                    created_at=stale,
                    sent_at=stale,
                )
            )
        # И само «свежее» сообщение диалога отодвигаем в прошлое.
        await session.execute(
            update(AiMessage)
            .where(AiMessage.conversation_id == conv_id)
            .values(created_at=stale)
        )
        await session.commit()

    fake.script = [AiResult(text="ок")]
    report = await _health(_resources, fake)
    assert ctx.clinic_id in report.silent_clinics


# ── 12. Идентификация пациента (§6.5) и V09 ─────────────────────────────────


async def test_new_patient_card_is_marked_as_assistant(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Карточка бота помечена source=assistant — администратор её проверит."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айгүл"})),
        AiResult(text=_reply_json("Записал имя, Айгүл.")),
    ]
    body = await _simulate(admin_client, ctx, "Меня зовут Айгүл", "+996700200080")
    assert body["outcome"] == "sent"

    async with _resources.factory() as session:
        patient = (
            await session.execute(select(Patient).where(Patient.phone == "+996700200080"))
        ).scalar_one()
    # Имя дословно: модель не «исправляет» Айгүл на Айгуль (§6.5).
    assert patient.first_name == "Айгүл"
    assert patient.source == "assistant"


async def test_foreign_phone_escalates_instead_of_guessing(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Пишут с чужого телефона: имя не сходится с карточкой — зовём человека.

    §6.5: угадывать нельзя, запись уйдёт не тому.
    """
    phone = "0700200081"
    created = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.owner_headers,
        json={"phone": phone, "firstName": "Нурлан", "lastName": "Осмонов"},
    )
    assert created.status_code == 201, created.text

    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айгуль"})),
        AiResult(text=_reply_json("Этого ответа быть не должно.")),
    ]
    body = await _simulate(admin_client, ctx, "Я Айгуль, запишите меня", f"+996{phone[1:]}")

    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "identity_mismatch"
    # Модель до ответа не дошла: эскалация решена детерминированно.
    assert len(fake.calls) == 1


async def test_same_name_returns_possible_duplicate(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Тот же человек под другим номером — пометка «возможно дубль» (§6.5)."""
    doctor_id = await own_membership_id(client, ctx.owner_headers)
    old = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.owner_headers,
        json={"phone": "0700200082", "firstName": "Бакыт", "lastName": "Токтогулов"},
    )
    assert old.status_code == 201, old.text
    # Пациент считается «этой клиники», когда у него есть здесь приём.
    booked = await client.post(
        f"{API}/clinic/appointments",
        headers=ctx.owner_headers,
        json={
            "patientId": old.json()["id"],
            "doctorMembershipId": doctor_id,
            "scheduledAt": _workday_ten(),
        },
    )
    assert booked.status_code == 201, booked.text

    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(
                name="create_patient",
                args={"firstName": "Бакыт", "lastName": "Токтогулов"},
            )
        ),
        AiResult(text=_reply_json("Записал вас.")),
    ]
    await _simulate(admin_client, ctx, "Я Бакыт Токтогулов", "+996700200083")

    result = _tool_result(fake.calls[1]["parts"], "create_patient")
    assert result["possibleDuplicate"] is True
    assert result["existing"] is False  # карточки бот не объединяет


async def test_other_clinic_mention_is_blocked(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """V09: упоминание чужой клиники не уходит пациенту."""
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    other = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Чужой Владелец",
            "adminPhone": _phone(),
            "name": "Стоматология Аврора",
            "cityId": city,
            "businessPhone": "0700000001",
            "seats": 3,
        },
    )
    assert other.status_code == 201, other.text

    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(text=_reply_json("Попробуйте Стоматология Аврора, там дешевле."))
    ]
    body = await _simulate(admin_client, ctx, "А где ещё лечат?", "+996700200084")
    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "validator_blocked"
