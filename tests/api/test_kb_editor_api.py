"""Ф6, инкременты 2–3: переписывание ответа моделью и редактор базы знаний.

Документ: создание с версией 1, правка = новая версия, откат = ещё одна
версия с пометкой, выключение без удаления; сводка токенов против порога
30 000; бот видит только активные документы, которые поместились в промпт.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.modules.ai.presentation.deps import get_ai_provider
from app.shared.ai import AiResult
from app.shared.db.models.clinic_usage_day import ClinicUsageDay
from tests.api.conftest import Ctx, _FakeProvider, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources

PARKING = "Есть ли у вас парковка?"


async def _create(client: AsyncClient, ctx: Ctx, **overrides: Any) -> Any:
    payload = {
        "title": "Как доехать",
        "category": "logistics",
        "body": "Вход со двора, въезд с улицы Токтогула. Парковка бесплатная.",
        "lang": "ru",
        **overrides,
    }
    resp = await client.post(f"{API}/clinic/kb", headers=ctx.owner_headers, json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _open_gap(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> str:
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Уточню у администратора.", needs_human=True))]
    body = await _simulate(admin_client, ctx, PARKING, "+996700300500")
    assert body["outcome"] == "escalated"
    gaps = await client.get(f"{API}/clinic/knowledge-gaps", headers=ctx.owner_headers)
    return str(gaps.json()["list"][0]["id"])


# ── Редактор ────────────────────────────────────────────────────────────────


async def test_create_gives_version_one_and_bot_sees_document(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    doc = await _create(client, ctx)
    assert doc["versionNo"] == 1 and doc["inPrompt"] is True and doc["isActive"] is True
    assert [v["action"] for v in doc["versions"]] == ["created"]
    assert doc["tokensEstimate"] > 0

    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Вход со двора, парковка бесплатная."))]
    await _simulate(admin_client, ctx, "Где вход?", "+996700300510")
    assert "въезд с улицы Токтогула" in fake.calls[-1]["system"]


async def test_prices_categories_and_langs_are_validated(client: AsyncClient, ctx: Ctx) -> None:
    async def attempt(**overrides: Any) -> Any:
        payload = {
            "title": "Т",
            "category": "faq",
            "body": "Текст документа",
            "lang": "ru",
            **overrides,
        }
        return await client.post(f"{API}/clinic/kb", headers=ctx.owner_headers, json=payload)

    priced = await attempt(body="Чистка стоит 2 500 сом, приходите.")
    assert priced.status_code == 422 and priced.json()["error"] == "kb_price_forbidden"
    category = await attempt(category="prices")
    assert category.status_code == 422 and category.json()["error"] == "kb_category_unknown"
    lang = await attempt(lang="en")
    assert lang.status_code == 422 and lang.json()["error"] == "kb_lang_unknown"


async def test_patch_makes_version_and_null_in_required_field_is_422(
    client: AsyncClient, ctx: Ctx
) -> None:
    doc = await _create(client, ctx)
    resp = await client.patch(
        f"{API}/clinic/kb/{doc['id']}",
        headers=ctx.owner_headers,
        json={"body": "Вход с улицы Токтогула, через арку. Парковка бесплатная."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["versionNo"] == 2
    assert [v["action"] for v in resp.json()["versions"]] == ["updated", "created"]
    assert resp.json()["versions"][1]["body"].startswith("Вход со двора")  # версия 1 цела

    nulled = await client.patch(
        f"{API}/clinic/kb/{doc['id']}", headers=ctx.owner_headers, json={"title": None}
    )
    assert nulled.status_code == 422
    priced = await client.patch(
        f"{API}/clinic/kb/{doc['id']}", headers=ctx.owner_headers, json={"body": "Приём 1 500 сом"}
    )
    assert priced.status_code == 422 and priced.json()["error"] == "kb_price_forbidden"


async def test_rollback_restores_content_as_new_version(client: AsyncClient, ctx: Ctx) -> None:
    doc = await _create(client, ctx)
    await client.patch(
        f"{API}/clinic/kb/{doc['id']}",
        headers=ctx.owner_headers,
        json={"body": "Новый текст документа"},
    )
    rolled = await client.post(
        f"{API}/clinic/kb/{doc['id']}/rollback", headers=ctx.owner_headers, json={"versionNo": 1}
    )
    assert rolled.status_code == 200, rolled.text
    assert rolled.json()["versionNo"] == 3
    assert rolled.json()["body"].startswith("Вход со двора")
    top = rolled.json()["versions"][0]
    assert top["action"] == "rolled_back" and top["note"] == "к версии 1"

    missing = await client.post(
        f"{API}/clinic/kb/{doc['id']}/rollback", headers=ctx.owner_headers, json={"versionNo": 9}
    )
    assert missing.status_code == 404 and missing.json()["error"] == "kb_version_not_found"


async def test_deactivated_document_leaves_prompt_and_usage(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    doc = await _create(client, ctx)
    listing = await client.get(f"{API}/clinic/kb", headers=ctx.owner_headers)
    assert listing.json()["usage"]["byLang"]["ru"]["docs"] == 1
    assert listing.json()["usage"]["byLang"]["ky"]["docs"] == 0
    assert listing.json()["usage"]["limitTokens"] == 30000

    off = await client.patch(
        f"{API}/clinic/kb/{doc['id']}", headers=ctx.owner_headers, json={"isActive": False}
    )
    assert off.status_code == 200 and off.json()["inPrompt"] is False

    listing = await client.get(
        f"{API}/clinic/kb", headers=ctx.owner_headers, params={"active": "true"}
    )
    assert listing.json()["totalCount"] == 0
    assert listing.json()["usage"]["byLang"]["ru"]["docs"] == 0

    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Подскажу у администратора."))]
    await _simulate(admin_client, ctx, "Где вход?", "+996700300520")
    assert "Токтогула" not in fake.calls[-1]["system"]


async def test_delete_is_final_and_foreign_ids_are_404(client: AsyncClient, ctx: Ctx) -> None:
    doc = await _create(client, ctx)
    gone = await client.delete(f"{API}/clinic/kb/{doc['id']}", headers=ctx.owner_headers)
    assert gone.status_code == 204
    after = await client.get(f"{API}/clinic/kb/{doc['id']}", headers=ctx.owner_headers)
    assert after.status_code == 404 and after.json()["error"] == "kb_document_not_found"
    foreign = await client.get(f"{API}/clinic/kb/{uuid.uuid4()}", headers=ctx.owner_headers)
    assert foreign.status_code == 404


async def test_search_and_filters(client: AsyncClient, ctx: Ctx) -> None:
    await _create(client, ctx)
    await _create(
        client,
        ctx,
        title="Тиш алгандан кийин",
        category="aftercare",
        lang="ky",
        body="Эки саат бою тамак жебеңиз, ысык суусундуктардан алыс болуңуз.",
    )
    ky = await client.get(f"{API}/clinic/kb", headers=ctx.owner_headers, params={"lang": "ky"})
    assert [d["title"] for d in ky.json()["list"]] == ["Тиш алгандан кийин"]
    found = await client.get(
        f"{API}/clinic/kb", headers=ctx.owner_headers, params={"q": "токтогула"}
    )
    assert found.json()["totalCount"] == 1
    aftercare = await client.get(
        f"{API}/clinic/kb", headers=ctx.owner_headers, params={"category": "aftercare"}
    )
    assert aftercare.json()["totalCount"] == 1


# ── Переписывание ответа моделью ───────────────────────────────────────────


async def test_rewrite_returns_proposal_and_meters_tokens(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    gap_id = await _open_gap(admin_client, client, ctx, fake)
    fake.script = [
        AiResult(
            text="```json\n"
            + json.dumps(
                {
                    "title": "Парковка",
                    "body": "У клиники есть бесплатная парковка во дворе, въезд с улицы Токтогула.",
                    "category": "logistics",
                    "notes": "Убрано обращение к пациенту",
                },
                ensure_ascii=False,
            )
            + "\n```",
            usage={"input": 120, "output": 40},
        )
    ]
    resp = await client.post(
        f"{API}/clinic/knowledge-gaps/{gap_id}/rewrite",
        headers=ctx.owner_headers,
        json={"answer": "да есть парковка бесплатная во дворе заезд с токтогула"},
    )
    assert resp.status_code == 200, resp.text
    proposal = resp.json()
    assert proposal["title"] == "Парковка" and proposal["category"] == "logistics"
    assert proposal["mentionsPrice"] is False and proposal["tokensEstimate"] > 0
    assert "Вопрос пациента: " + PARKING in fake.calls[-1]["parts"][0].text
    assert "на русском языке" in fake.calls[-1]["system"]

    # Ничего не сохранено: очередь прежняя, документов нет; токены учтены клинике.
    gaps = await client.get(f"{API}/clinic/knowledge-gaps", headers=ctx.owner_headers)
    assert gaps.json()["counts"]["unknown"] == 1
    kb = await client.get(f"{API}/clinic/kb", headers=ctx.owner_headers)
    assert kb.json()["totalCount"] == 0
    async with _resources.factory() as session:
        usage = (await session.execute(select(ClinicUsageDay))).scalars().all()
    assert sum(u.model_input_tokens for u in usage) >= 120

    # Подтверждение — обычный answer с (возможно, правленым) текстом.
    confirmed = await client.post(
        f"{API}/clinic/knowledge-gaps/{gap_id}/answer",
        headers=ctx.owner_headers,
        json={
            "answer": proposal["body"],
            "title": proposal["title"],
            "category": proposal["category"],
        },
    )
    assert confirmed.status_code == 201
    doc = await client.get(
        f"{API}/clinic/kb/{confirmed.json()['kbDocumentId']}", headers=ctx.owner_headers
    )
    assert doc.status_code == 200 and doc.json()["versionNo"] == 1


async def test_rewrite_flags_leftover_price_and_survives_bad_json(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    gap_id = await _open_gap(admin_client, client, ctx, fake)
    fake.script = [
        AiResult(text='{"title": "Парковка", "body": "Парковка 100 сом в час.", "category": "faq"}')
    ]
    priced = await client.post(
        f"{API}/clinic/knowledge-gaps/{gap_id}/rewrite",
        headers=ctx.owner_headers,
        json={"answer": "парковка 100 сом в час"},
    )
    assert priced.status_code == 200 and priced.json()["mentionsPrice"] is True
    assert "цена" in priced.json()["notes"]

    fake.script = [AiResult(text="Не могу, извините.")]
    raw = await client.post(
        f"{API}/clinic/knowledge-gaps/{gap_id}/rewrite",
        headers=ctx.owner_headers,
        json={"answer": "парковка есть во дворе"},
    )
    assert raw.status_code == 200
    assert raw.json()["body"] == "парковка есть во дворе" and raw.json()["title"] == PARKING
    assert "как есть" in raw.json()["notes"]


async def test_rewrite_without_model_is_502(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    from app.main import app

    gap_id = await _open_gap(admin_client, client, ctx, fake)
    app.dependency_overrides[get_ai_provider] = lambda: None
    try:
        resp = await client.post(
            f"{API}/clinic/knowledge-gaps/{gap_id}/rewrite",
            headers=ctx.owner_headers,
            json={"answer": "парковка есть"},
        )
    finally:
        app.dependency_overrides[get_ai_provider] = lambda: fake
    assert resp.status_code == 502 and resp.json()["error"] == "ai_unavailable"
