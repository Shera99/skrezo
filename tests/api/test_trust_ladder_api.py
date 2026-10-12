"""Ф7, инкремент 1: ступени доверия — Наблюдаю → Проверяю → Доверяю.

Вверх — только через гейт (цифры теневого режима, одобрения, чистая неделя),
вниз и «выключить» — всегда; выключение помнит заработанную ступень;
команда Skrezo обходит гейт, и владелец видит, что менял не он.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.shared.ai import AiResult
from app.shared.db.models.ai_message import AiMessage
from tests.api.conftest import Ctx, _FakeProvider, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources


async def _ladder(client: AsyncClient, ctx: Ctx) -> Any:
    resp = await client.get(f"{API}/clinic/assistant/trust", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _set(client: AsyncClient, ctx: Ctx, **body: Any) -> Any:
    return await client.put(f"{API}/clinic/assistant/trust", headers=ctx.owner_headers, json=body)


async def _approve_one(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    res: Resources,
    phone: str,
) -> None:
    """Один одобренный черновик в режиме «Проверяю»."""
    fake.script = [AiResult(text=_reply_json("Приходите, мы вас ждём."))]
    body = await _simulate(admin_client, ctx, "Можно прийти без записи?", phone)
    assert body["outcome"] == "draft", body
    async with res.factory() as session:
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
    )
    assert sent.status_code == 201, sent.text


async def test_new_clinic_is_off_with_first_step_open(client: AsyncClient, ctx: Ctx) -> None:
    ladder = await _ladder(client, ctx)
    assert ladder["mode"] == "off" and ladder["enabled"] is False and ladder["level"] is None
    assert [s["level"] for s in ladder["steps"]] == ["shadow", "assisted", "auto"]
    shadow, assisted, auto = ladder["steps"]
    assert (
        shadow["title"] == "Наблюдаю" and shadow["ready"] is True and shadow["requirements"] == []
    )
    assert assisted["ready"] is False
    assert assisted["requirements"][0]["key"] == "shadow_replies"
    assert assisted["requirements"][0]["required"] == settings.ai_trust_gate_shadow_replies
    assert (
        auto["requirements"][0]["key"] == "approvals" and auto["requirements"][0]["required"] == 200
    )
    assert ladder["next"]["level"] == "shadow"
    assert ladder["stats"] == {
        "shadowReplies": 0,
        "approvals": 0,
        "factErrors": 0,
        "factErrorDays": 7,
    }


async def test_climbing_requires_gates_and_descending_is_free(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Включить — «Наблюдаю», без условий.
    on = await _set(client, ctx, enabled=True)
    assert (
        on.status_code == 200 and on.json()["level"] == "shadow" and on.json()["reason"] == "manual"
    )
    assert on.json()["changedAt"] is not None and on.json()["next"]["level"] == "assisted"

    # «Проверяю» закрыта: в тени ещё ничего не прошло.
    closed = await _set(client, ctx, level="assisted")
    assert closed.status_code == 409, closed.text
    assert closed.json()["error"] == "trust_gate_closed" and closed.json()["level"] == "assisted"
    assert closed.json()["blockers"] == [
        "ответов бота, прошедших валидатор в режиме «Наблюдаю»: 0 из "
        f"{settings.ai_trust_gate_shadow_replies}"
    ]

    # Один теневой ответ при пороге 1 — ступень открылась.
    monkeypatch.setattr(settings, "ai_trust_gate_shadow_replies", 1)
    fake.script = [AiResult(text=_reply_json("Мы работаем ежедневно."))]
    body = await _simulate(admin_client, ctx, "Вы работаете по субботам?", "+996700600500")
    assert body["outcome"] == "shadow"
    assert (await _ladder(client, ctx))["steps"][1]["ready"] is True
    up = await _set(client, ctx, level="assisted")
    assert up.status_code == 200 and up.json()["level"] == "assisted"

    # «Доверяю» закрыта: одобрений 0 из 200.
    closed = await _set(client, ctx, level="auto")
    assert closed.status_code == 409 and "0 из 200" in closed.json()["message"]

    monkeypatch.setattr(settings, "ai_trust_gate_approvals", 1)
    await _approve_one(admin_client, client, ctx, fake, _resources, "+996700600501")
    up = await _set(client, ctx, level="auto")
    assert up.status_code == 200 and up.json()["level"] == "auto" and up.json()["next"] is None

    # Фактическая ошибка (время не из фактов) — неделя не чистая: вниз можно, обратно вверх — нет.
    invented = AiResult(text=_reply_json("Парковка работает с 08:00.", facts=[]))
    fake.script = [invented, invented]  # повтор с подсказкой тоже выдумал — блок
    body = await _simulate(admin_client, ctx, "Во сколько открывается парковка?", "+996700600502")
    assert body["outcome"] == "escalated"
    down = await _set(client, ctx, level="shadow")
    assert down.status_code == 200 and down.json()["level"] == "shadow"
    blocked = await _set(client, ctx, level="auto")
    assert blocked.status_code == 409
    assert any("фактических ошибок" in b for b in blocked.json()["blockers"])
    assert (await _ladder(client, ctx))["stats"]["factErrors"] == 1


async def test_switching_off_remembers_level_and_admin_override_is_visible(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "auto")  # Skrezo обходит гейт
    ladder = await _ladder(client, ctx)
    assert ladder["level"] == "auto" and ladder["reason"] == "admin"

    off = await _set(client, ctx, enabled=False)
    assert off.status_code == 200
    assert off.json()["enabled"] is False and off.json()["levelBeforeOff"] == "auto"
    assert off.json()["next"]["level"] == "auto"  # включение вернёт заработанное

    on = await _set(client, ctx, enabled=True)
    assert (
        on.status_code == 200 and on.json()["level"] == "auto" and on.json()["reason"] == "manual"
    )

    # Пустой запрос — 422; повтор текущей ступени — без изменений.
    empty = await client.put(f"{API}/clinic/assistant/trust", headers=ctx.owner_headers, json={})
    assert empty.status_code == 422
    same = await _set(client, ctx, level="auto")
    assert same.status_code == 200 and same.json()["level"] == "auto"
