"""Ф6, инкремент 4: регрессия из выученных ответов.

Кейсы рождаются сами — из ответа на пробел, отправленного черновика, снятой
валидатором выдумки и сработавшего медфильтра; прогон без модели проверяет
детерминированное, прогон с моделью — узнаваемость ответа по ключевым словам.
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

import app.jobs.tasks.ai as ai_tasks
from app.shared.ai import AiResult
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.ai_regression_run import AiRegressionRun
from tests.api.conftest import Ctx, _FakeProvider, _patch_job, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources

PARKING = "Есть ли у вас парковка?"
ANSWER = "Да, бесплатная парковка во дворе, въезд с улицы Токтогула."


async def _cases(client: AsyncClient, ctx: Ctx, **params: Any) -> Any:
    resp = await client.get(
        f"{API}/clinic/regression/cases", headers=ctx.owner_headers, params=params
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _run(client: AsyncClient, ctx: Ctx, mode: str = "offline") -> Any:
    resp = await client.post(
        f"{API}/clinic/regression/runs", headers=ctx.owner_headers, json={"mode": mode}
    )
    assert resp.status_code == 202, resp.text
    detail = await client.get(
        f"{API}/clinic/regression/runs/{resp.json()['id']}", headers=ctx.owner_headers
    )
    assert detail.status_code == 200, detail.text
    return detail.json()


async def _learn_parking(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider, phone: str
) -> str:
    """Пробел → ответ администратора → документ; вернуть id документа."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Уточню у администратора.", needs_human=True))]
    body = await _simulate(admin_client, ctx, PARKING, phone)
    assert body["outcome"] == "escalated"
    gaps = await client.get(f"{API}/clinic/knowledge-gaps", headers=ctx.owner_headers)
    gap_id = gaps.json()["list"][0]["id"]
    answered = await client.post(
        f"{API}/clinic/knowledge-gaps/{gap_id}/answer",
        headers=ctx.owner_headers,
        json={"answer": ANSWER, "category": "logistics"},
    )
    assert answered.status_code == 201, answered.text
    return str(answered.json()["kbDocumentId"])


# ── Источники кейсов и прогон без модели ─────────────────────────────────────


async def test_learned_answer_becomes_case_and_offline_run_checks_the_prompt(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    doc_id = await _learn_parking(admin_client, client, ctx, fake, "+996700400500")

    cases = await _cases(client, ctx)
    assert cases["counts"] == {"total": 1, "active": 1, "failing": 0}
    case = cases["list"][0]
    assert case["kind"] == "knowledge" and case["source"] == "knowledge_gap"
    assert case["question"] == PARKING and case["lang"] == "ru"
    assert case["expect"]["kbDocumentId"] == doc_id
    assert "парковка" in case["expect"]["keywords"] and "токтогула" in case["expect"]["keywords"]
    assert case["origin"]["kbDocumentId"] == doc_id

    run = await _run(client, ctx)
    assert run["status"] == "done" and run["mode"] == "offline"
    assert (run["total"], run["passed"], run["failed"], run["skipped"]) == (1, 1, 0, 0)
    assert run["promptVersion"] and run["modelName"] == "fake-model"
    assert run["results"][0]["status"] == "passed" and "активен" in run["results"][0]["actual"]
    assert (await _cases(client, ctx))["list"][0]["lastResult"] == "passed"

    # Выключили документ — регрессия ловит: бот больше не знает ответа.
    off = await client.patch(
        f"{API}/clinic/kb/{doc_id}", headers=ctx.owner_headers, json={"isActive": False}
    )
    assert off.status_code == 200
    run = await _run(client, ctx)
    assert run["failed"] == 1 and "выключен" in run["results"][0]["actual"]
    assert (await _cases(client, ctx))["counts"]["failing"] == 1

    runs = await client.get(f"{API}/clinic/regression/runs", headers=ctx.owner_headers)
    assert runs.json()["totalCount"] == 2 and runs.json()["list"][0]["id"] == run["id"]


async def test_blocked_invention_becomes_refusal_case_that_validator_keeps_blocking(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Парковка работает с 08:00.", facts=[]))]
    body = await _simulate(admin_client, ctx, "Во сколько открывается парковка?", "+996700400510")
    assert body["outcome"] == "escalated"

    case = (await _cases(client, ctx, kind="refusal"))["list"][0]
    assert case["source"] == "incident"
    assert case["expect"]["proposed"] == "Парковка работает с 08:00."
    assert case["expect"]["codes"]

    run = await _run(client, ctx)
    result = next(r for r in run["results"] if r["kind"] == "refusal")
    assert result["status"] == "passed"
    assert result["actual"].startswith("block/")


async def test_medical_filter_becomes_case(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    body = await _simulate(admin_client, ctx, "Опухла щека и температура 38", "+996700400520")
    assert body["outcome"] == "escalated"

    case = (await _cases(client, ctx, kind="medical"))["list"][0]
    assert case["expect"] == {"medical": "urgent"} and case["source"] == "medical"
    run = await _run(client, ctx)
    assert run["passed"] == run["total"] == 1
    assert run["results"][0]["actual"] == "urgent"


async def test_sent_draft_becomes_reply_case_and_model_run_scores_it(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "assisted")
    fake.script = [AiResult(text=_reply_json("Уточню у администратора и напишу."))]
    body = await _simulate(admin_client, ctx, PARKING, "+996700400530")
    assert body["outcome"] == "draft", body
    async with _resources.factory() as session:
        draft_id = (
            await session.execute(
                select(AiMessage.id).where(
                    AiMessage.conversation_id == uuid.UUID(body["conversationId"]),
                    AiMessage.review == "pending",
                )
            )
        ).scalar_one()
    sent = await client.post(
        f"{API}/clinic/conversations/{body['conversationId']}/drafts/{draft_id}/send",
        headers=ctx.owner_headers,
        json={"body": ANSWER},
    )
    assert sent.status_code == 201, sent.text

    case = (await _cases(client, ctx, kind="reply"))["list"][0]
    assert case["source"] == "edited_draft" and case["question"] == PARKING
    assert case["expect"]["reply"] == ANSWER
    assert case["origin"]["conversationId"] == body["conversationId"]

    # Без модели такой кейс не проверить — честно skipped, не failed.
    run = await _run(client, ctx)
    assert run["skipped"] == 1 and "нужна модель" in run["results"][0]["note"]

    # Сухой прогон модели: ответ узнаваем по ключевым словам эталона.
    fake.script = [
        AiResult(
            text=_reply_json("Да, у нас бесплатная парковка во дворе, въезд с улицы Токтогула.")
        )
    ]
    run = await _run(client, ctx, mode="model")
    assert run["status"] == "done" and run["passed"] == 1, run
    assert "ключевые слова" in run["results"][0]["note"]
    assert fake.calls[-1]["parts"][0].text == PARKING  # вопрос кейса, а не история диалога

    # Ответил не о том — кейс красный, с текстом ответа в actual.
    fake.script = [AiResult(text=_reply_json("Мы работаем ежедневно, приходите."))]
    run = await _run(client, ctx, mode="model")
    assert run["failed"] == 1 and run["results"][0]["actual"].startswith("Мы работаем")


async def test_model_run_fails_knowledge_case_when_bot_declines_and_passes_refusal(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    await _learn_parking(admin_client, client, ctx, fake, "+996700400540")
    fake.script = [AiResult(text=_reply_json("Парковка работает с 08:00.", facts=[]))]
    await _simulate(admin_client, ctx, "Во сколько открывается парковка?", "+996700400541")
    assert (await _cases(client, ctx))["counts"]["active"] == 2

    # knowledge → «не знаю» = провал; refusal → «не знаю» = норма.
    fake.script = [
        AiResult(text=_reply_json("Уточню у администратора.", needs_human=True)),
        AiResult(text=_reply_json("Уточню у администратора.", needs_human=True)),
    ]
    run = await _run(client, ctx, mode="model")
    by_kind = {r["kind"]: r for r in run["results"]}
    assert (
        by_kind["knowledge"]["status"] == "failed"
        and by_kind["knowledge"]["actual"] == "needs_human"
    )
    assert by_kind["refusal"]["status"] == "passed"


# ── Ручные кейсы, дедупликация, занятость ────────────────────────────────────


async def test_manual_cases_dedupe_toggle_delete_and_busy_run(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    payload = {"kind": "reply", "lang": "ru", "question": PARKING, "expectedReply": ANSWER}
    created = await client.post(
        f"{API}/clinic/regression/cases", headers=ctx.owner_headers, json=payload
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"] == "manual" and created.json()["expect"]["keywords"]

    again = await client.post(
        f"{API}/clinic/regression/cases",
        headers=ctx.owner_headers,
        json={**payload, "question": "есть ли у вас ПАРКОВКА?!"},
    )
    assert again.status_code == 201 and again.json()["id"] == created.json()["id"]
    assert (await _cases(client, ctx))["totalCount"] == 1

    bad = await client.post(
        f"{API}/clinic/regression/cases",
        headers=ctx.owner_headers,
        json={"kind": "medical", "lang": "ru", "question": "Болит зуб"},
    )
    assert bad.status_code == 422 and bad.json()["error"] == "case_expect_invalid"

    case_id = created.json()["id"]
    off = await client.patch(
        f"{API}/clinic/regression/cases/{case_id}",
        headers=ctx.owner_headers,
        json={"isActive": False},
    )
    assert off.status_code == 200 and off.json()["isActive"] is False
    assert (await _run(client, ctx))["total"] == 0  # выключенный в прогон не идёт

    async with _resources.factory() as session:
        session.add(
            AiRegressionRun(
                clinic_id=uuid.UUID(ctx.clinic_id),
                mode="model",
                status="running",
                prompt_version="v1",
            )
        )
        await session.commit()
    busy = await client.post(
        f"{API}/clinic/regression/runs", headers=ctx.owner_headers, json={"mode": "offline"}
    )
    assert busy.status_code == 409 and busy.json()["error"] == "regression_run_active"

    gone = await client.delete(
        f"{API}/clinic/regression/cases/{case_id}", headers=ctx.owner_headers
    )
    assert gone.status_code == 204
    missing = await client.patch(
        f"{API}/clinic/regression/cases/{case_id}", headers=ctx.owner_headers, json={"note": "x"}
    )
    assert missing.status_code == 404 and missing.json()["error"] == "case_not_found"


async def test_worker_job_runs_queued_run(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: Any,
) -> None:
    await _learn_parking(admin_client, client, ctx, fake, "+996700400550")
    async with _resources.factory() as session:
        run = AiRegressionRun(
            clinic_id=uuid.UUID(ctx.clinic_id), mode="offline", status="queued", prompt_version="v1"
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    summary = await ai_tasks.run_ai_regression({}, str(run_id))
    assert "1 ok / 0 fail / 0 skip" in summary

    detail = await client.get(f"{API}/clinic/regression/runs/{run_id}", headers=ctx.owner_headers)
    assert detail.json()["status"] == "done" and detail.json()["passed"] == 1
    # Повтор задания ничего не пересчитывает.
    assert "уже завершён" in await ai_tasks.run_ai_regression({}, str(run_id))
