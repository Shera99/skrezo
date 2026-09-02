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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select

from app.main import app
from app.modules.ai.presentation.deps import get_ai_provider
from app.shared.ai import AiMessagePart, AiResult, AiToolDecl, ToolCallRequest
from app.shared.db.models.ai_tool_call import AiToolCall
from app.shared.db.models.event import Event
from tests.conftest import API, Resources, otp_login

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


async def test_auto_sends_reply(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
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
    fake.script = [
        AiResult(text=_reply_json("Уточню у администратора.", needs_human=True))
    ]
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

    pending = await client.get(
        f"{API}/clinic/glossary?status=pending", headers=ctx.owner_headers
    )
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
    deleted = await client.delete(
        f"{API}/clinic/glossary/{term_id}", headers=ctx.owner_headers
    )
    assert deleted.status_code == 204
