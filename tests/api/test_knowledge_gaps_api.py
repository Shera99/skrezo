"""Ф6, инкремент 1: пробелы знаний — очередь экрана «Обучение».

Бот не ответил → строка очереди со счётчиком; похожие формулировки склеены;
администратор отвечает своими словами → документ базы знаний → бот знает со
следующего хода; «не нужно отвечать» убирает вопрос из очереди навсегда.
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.shared.ai import AiResult, ToolCallRequest
from app.shared.db.models.ai_knowledge_gap import AiKnowledgeGap
from app.shared.db.models.kb_document import KbDocument
from tests.api.conftest import Ctx, _FakeProvider, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources

PARKING = "Есть ли у вас парковка?"


async def _dont_know(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, text: str, phone: str
) -> Any:
    fake.script = [
        AiResult(
            text=_reply_json(
                "Уточню у администратора.", needs_human=True, human_reason="нет данных о парковке"
            )
        )
    ]
    body = await _simulate(admin_client, ctx, text, phone)
    assert body["outcome"] == "escalated", body
    return body


async def _gaps(client: AsyncClient, ctx: Ctx, **params: Any) -> Any:
    resp = await client.get(
        f"{API}/clinic/knowledge-gaps", headers=ctx.owner_headers, params=params
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Фиксация ────────────────────────────────────────────────────────────────


async def test_honest_dont_know_opens_a_gap(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    body = await _dont_know(admin_client, ctx, fake, PARKING, "+996700200500")

    gaps = await _gaps(client, ctx)
    assert gaps["counts"] == {"unknown": 1, "answered": 0, "dismissed": 0}
    gap = gaps["list"][0]
    assert gap["question"] == PARKING
    assert gap["status"] == "unknown" and gap["source"] == "low_confidence"
    assert gap["hint"] == "нет данных о парковке"
    assert gap["askCount"] == 1
    assert gap["conversationIds"] == [body["conversationId"]]


async def test_similar_questions_are_merged_with_counter(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """Регистр, пунктуация, опечатка — один вопрос, «спросили ×3»."""
    await _set_mode(admin_client, ctx, "auto")
    first = await _dont_know(admin_client, ctx, fake, PARKING, "+996700200510")
    second = await _dont_know(admin_client, ctx, fake, "есть ли у вас ПАРКОВКА?!", "+996700200511")
    third = await _dont_know(admin_client, ctx, fake, "Есть ли у вас парковкa", "+996700200512")

    gaps = await _gaps(client, ctx)
    assert gaps["totalCount"] == 1
    gap = gaps["list"][0]
    assert gap["askCount"] == 3
    assert gap["question"] == PARKING  # первая формулировка остаётся лицом вопроса
    assert set(gap["conversationIds"]) == {
        first["conversationId"],
        second["conversationId"],
        third["conversationId"],
    }


async def test_specifics_without_facts_is_a_gap_too(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    """Валидатор заблокировал ответ без единого факта — знаний не было."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Парковка работает с 08:00.", facts=[]))]
    body = await _simulate(admin_client, ctx, "Во сколько открывается парковка?", "+996700200520")
    assert body["outcome"] == "escalated"

    gap = (await _gaps(client, ctx))["list"][0]
    assert gap["source"] == "no_facts"
    assert gap["question"] == "Во сколько открывается парковка?"


async def test_model_escalate_unknown_is_a_gap_but_patient_asked_is_not(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(
                name="escalate", args={"reason": "unknown", "note": "нет в базе"}
            )
        )
    ]
    await _simulate(admin_client, ctx, "Вы принимаете по ОМС?", "+996700200530")
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="escalate", args={"reason": "patient_asked"}))
    ]
    await _simulate(admin_client, ctx, "Позовите живого человека", "+996700200531")

    gaps = await _gaps(client, ctx)
    assert gaps["totalCount"] == 1
    assert gaps["list"][0]["source"] == "model_unknown"
    assert gaps["list"][0]["hint"] == "нет в базе"


# ── Решения ─────────────────────────────────────────────────────────────────


async def test_answer_becomes_knowledge_and_bot_sees_it(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    await _dont_know(admin_client, ctx, fake, PARKING, "+996700200540")
    gap_id = (await _gaps(client, ctx))["list"][0]["id"]

    answered = await client.post(
        f"{API}/clinic/knowledge-gaps/{gap_id}/answer",
        headers=ctx.owner_headers,
        json={
            "answer": "Да, бесплатная парковка во дворе, въезд с улицы Токтогула.",
            "category": "logistics",
        },
    )
    assert answered.status_code == 201, answered.text
    assert answered.json()["status"] == "answered"
    assert answered.json()["kbDocumentId"] is not None
    assert (await _gaps(client, ctx))["counts"] == {"unknown": 0, "answered": 1, "dismissed": 0}

    async with _resources.factory() as session:
        doc = (await session.execute(select(KbDocument))).scalar_one()
    assert doc.title == PARKING and doc.category == "logistics" and doc.lang == "ru"
    assert doc.is_active is True

    # Следующий ход: знание уже в системном промпте.
    fake.script = [AiResult(text=_reply_json("Да, у нас есть бесплатная парковка во дворе."))]
    body = await _simulate(admin_client, ctx, "Где припарковаться?", "+996700200541")
    assert body["outcome"] == "sent"
    assert "въезд с улицы Токтогула" in fake.calls[-1]["system"]

    # Повторное решение по закрытому вопросу — конфликт.
    again = await client.post(
        f"{API}/clinic/knowledge-gaps/{gap_id}/dismiss", headers=ctx.owner_headers
    )
    assert again.status_code == 409 and again.json()["error"] == "knowledge_gap_resolved"


async def test_prices_are_not_allowed_in_answers(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    await _dont_know(admin_client, ctx, fake, "Сколько стоит парковка?", "+996700200550")
    gap_id = (await _gaps(client, ctx))["list"][0]["id"]

    resp = await client.post(
        f"{API}/clinic/knowledge-gaps/{gap_id}/answer",
        headers=ctx.owner_headers,
        json={"answer": "Парковка стоит 200 сом в час."},
    )
    assert resp.status_code == 422 and resp.json()["error"] == "kb_price_forbidden"
    assert (await _gaps(client, ctx))["counts"]["unknown"] == 1  # вопрос остался в очереди


async def test_dismiss_removes_from_queue_for_good(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    await _dont_know(admin_client, ctx, fake, "Какой у вас режим на Курман айт?", "+996700200560")
    gap_id = (await _gaps(client, ctx))["list"][0]["id"]

    resp = await client.post(
        f"{API}/clinic/knowledge-gaps/{gap_id}/dismiss", headers=ctx.owner_headers
    )
    assert resp.status_code == 200 and resp.json()["status"] == "dismissed"
    assert (await _gaps(client, ctx, status="unknown"))["list"] == []
    assert (await _gaps(client, ctx))["counts"]["dismissed"] == 1

    # Тот же вопрос снова: отклонённый не воскресает, открывается новый пробел.
    await _dont_know(admin_client, ctx, fake, "Какой у вас режим на Курман айт?", "+996700200561")
    async with _resources.factory() as session:
        statuses = sorted(
            g.status for g in (await session.execute(select(AiKnowledgeGap))).scalars()
        )
    assert statuses == ["dismissed", "unknown"]


async def test_foreign_gap_is_404(client: AsyncClient, ctx: Ctx) -> None:
    resp = await client.post(
        f"{API}/clinic/knowledge-gaps/{uuid.uuid4()}/dismiss", headers=ctx.owner_headers
    )
    assert resp.status_code == 404 and resp.json()["error"] == "knowledge_gap_not_found"
