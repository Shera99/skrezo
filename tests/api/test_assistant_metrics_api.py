"""Ф6, инкремент 5: метрики обучения и гейт доверия.

Один экран отвечает «учится ли бот»: авто-ответы, черновики с правками,
эскалации и фактические ошибки, очередь «Обучение», регрессия, токены —
и гейт «N одобренных и ноль ошибок V01–V03 за неделю» для ступеней Ф7.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.shared.ai import AiResult
from app.shared.db.models.ai_message import AiMessage
from tests.api.conftest import Ctx, _FakeProvider, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources

PARKING = "Есть ли у вас парковка?"


async def _metrics(client: AsyncClient, ctx: Ctx, **params: Any) -> Any:
    resp = await client.get(
        f"{API}/clinic/assistant/metrics", headers=ctx.owner_headers, params=params
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _draft_id(res: Resources, conversation_id: str) -> int:
    async with res.factory() as session:
        return int(
            (
                await session.execute(
                    select(AiMessage.id).where(
                        AiMessage.conversation_id == uuid.UUID(conversation_id),
                        AiMessage.review == "pending",
                    )
                )
            ).scalar_one()
        )


async def test_empty_clinic_has_zero_metrics_and_closed_gate(client: AsyncClient, ctx: Ctx) -> None:
    m = await _metrics(client, ctx)
    assert m["period"]["period"] == "1m" and m["period"]["timezone"]
    assert m["autonomy"]["dialogs"] == 0 and m["autonomy"]["autonomyShare"] == 0
    assert m["learning"]["gapsOpen"] == 0 and m["regression"]["lastRun"] is None
    assert m["usage"] == {"modelCalls": 0, "inputTokens": 0, "outputTokens": 0}
    assert m["trustGate"]["approved"] == 0 and m["trustGate"]["ready"] is False
    assert m["trustGate"]["required"] == settings.ai_trust_gate_approvals
    assert m["series"] == []


async def test_metrics_reflect_auto_replies_drafts_escalations_and_learning(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    # 1) auto: ответ без человека
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(text=_reply_json("Мы работаем ежедневно."), usage={"input": 50, "output": 10})
    ]
    body = await _simulate(admin_client, ctx, "Вы работаете по субботам?", "+996700500500")
    assert body["outcome"] == "sent"

    # 2) auto: фактическая ошибка — время не из фактов (V02) → блок + эскалация + пробел
    fake.script = [AiResult(text=_reply_json("Парковка работает с 08:00.", facts=[]))]
    body = await _simulate(admin_client, ctx, "Во сколько открывается парковка?", "+996700500501")
    assert body["outcome"] == "escalated"

    # 3) auto: честное «не знаю» → эскалация + пробел
    fake.script = [AiResult(text=_reply_json("Уточню у администратора.", needs_human=True))]
    body = await _simulate(admin_client, ctx, PARKING, "+996700500502")
    assert body["outcome"] == "escalated"

    # 4) assisted: черновик отправлен с правкой, второй — отклонён
    await _set_mode(admin_client, ctx, "assisted")
    fake.script = [AiResult(text=_reply_json("Приходите, мы вас ждём."))]
    body = await _simulate(admin_client, ctx, "Можно прийти без записи?", "+996700500503")
    assert body["outcome"] == "draft"
    draft = await _draft_id(_resources, body["conversationId"])
    sent = await client.post(
        f"{API}/clinic/conversations/{body['conversationId']}/drafts/{draft}/send",
        headers=ctx.owner_headers,
        json={"body": "Без записи принимаем при наличии окна — напишите, когда удобно."},
    )
    assert sent.status_code == 201, sent.text
    fake.script = [AiResult(text=_reply_json("Да, конечно."))]
    body = await _simulate(admin_client, ctx, "А в воскресенье?", "+996700500504")
    draft = await _draft_id(_resources, body["conversationId"])
    discarded = await client.post(
        f"{API}/clinic/conversations/{body['conversationId']}/drafts/{draft}/discard",
        headers=ctx.owner_headers,
    )
    assert discarded.status_code == 200, discarded.text

    # 5) обучение: ответили на пробел «парковка» → документ + кейс регрессии; прогон
    gaps = await client.get(f"{API}/clinic/knowledge-gaps", headers=ctx.owner_headers)
    parking = next(g for g in gaps.json()["list"] if g["question"] == PARKING)
    answered = await client.post(
        f"{API}/clinic/knowledge-gaps/{parking['id']}/answer",
        headers=ctx.owner_headers,
        json={"answer": "Да, бесплатная парковка во дворе.", "category": "logistics"},
    )
    assert answered.status_code == 201
    run = await client.post(
        f"{API}/clinic/regression/runs", headers=ctx.owner_headers, json={"mode": "offline"}
    )
    assert run.status_code == 202

    m = await _metrics(client, ctx, period="7d")
    a = m["autonomy"]
    assert a["dialogs"] == 5
    assert a["repliesAuto"] == 1 and a["draftsSent"] == 1 and a["draftsEdited"] == 1
    assert a["draftsDiscarded"] == 1 and a["draftsPending"] == 0
    assert a["escalations"] == 2
    assert a["autonomyShare"] == 0.25  # 1 / (1 + 1 + 2)
    assert a["editShare"] == 1.0

    q = m["quality"]
    assert q["validatorBlocked"] == 1 and q["factErrors"] == 1 and q["needsHuman"] == 1
    assert q["escalationsByReason"] == {"validator_blocked": 1, "low_confidence": 1}

    learning = m["learning"]
    assert (
        learning["gapsOpened"] == 2 and learning["gapsAnswered"] == 1 and learning["gapsOpen"] == 1
    )
    assert learning["kbDocumentsActive"] == 1 and learning["kbDocumentsAdded"] == 1
    assert learning["avgHoursToAnswer"] >= 0

    r = m["regression"]
    assert r["casesActive"] == 3  # knowledge + refusal + reply
    assert r["lastRun"]["status"] == "done" and r["lastRun"]["mode"] == "offline"

    assert m["usage"]["modelCalls"] >= 4 and m["usage"]["inputTokens"] >= 50

    gate = m["trustGate"]
    assert gate["mode"] == "assisted" and gate["approved"] == 1
    assert gate["factErrors"] == 1 and gate["ready"] is False
    assert gate["progress"] == round(1 / settings.ai_trust_gate_approvals, 3)

    today = datetime.now(UTC).date().isoformat()
    point = next(p for p in m["series"] if p["date"] >= today[:8] + "01")
    assert point["repliesAuto"] == 1 and point["draftsSent"] == 1
    assert point["escalations"] == 2 and point["gapsOpened"] == 2 and point["gapsAnswered"] == 1


async def test_gate_opens_when_approvals_reach_threshold_without_fact_errors(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _set_mode(admin_client, ctx, "assisted")
    fake.script = [AiResult(text=_reply_json("Приходите, мы вас ждём."))]
    body = await _simulate(admin_client, ctx, "Можно прийти без записи?", "+996700500510")
    draft = await _draft_id(_resources, body["conversationId"])
    sent = await client.post(
        f"{API}/clinic/conversations/{body['conversationId']}/drafts/{draft}/send",
        headers=ctx.owner_headers,
    )
    assert sent.status_code == 201

    monkeypatch.setattr(settings, "ai_trust_gate_approvals", 1)
    gate = (await _metrics(client, ctx))["trustGate"]
    assert gate["approved"] == 1 and gate["required"] == 1 and gate["progress"] == 1.0
    assert gate["ready"] is True

    # Явный диапазон дат перекрывает пресет; период без активности — нули, гейт всё тот же.
    m = await _metrics(client, ctx, dateFrom="2020-01-01", dateTo="2020-01-31")
    assert m["period"]["period"] is None and m["period"]["dateFrom"] == "2020-01-01"
    assert m["autonomy"]["draftsSent"] == 0 and m["trustGate"]["ready"] is True
