"""Ф5, инкремент 2: черновик как объект — режим «Проверяю» по-настоящему.

Панель «Ассистент предлагает» из макета: бот готовит черновик, сотрудник
отправляет как есть, отправляет с правкой или отклоняет. Плюс переключатель
«дальше отвечаю сам» / «вернуть боту» и защита от ответа на устаревшую реплику.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.shared.ai import AiResult
from app.shared.db.enums import AiConversationStatus
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.event import Event
from tests.api.conftest import (
    Ctx,
    _FakeProvider,
    _inbound,
    _reply_json,
    _set_mode,
    _simulate,
    _webhook,
)
from tests.conftest import API, Resources

DRAFT_TEXT = "Здравствуйте! Подскажу по чистке — уточню цену и свободное время."


async def _draft(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, res: Resources, phone: str, text: str
) -> tuple[str, int]:
    """Прогнать сообщение в режиме «Проверяю»; вернуть (диалог, id черновика)."""
    fake.script = [AiResult(text=_reply_json(DRAFT_TEXT))]
    body = await _simulate(admin_client, ctx, text, phone)
    assert body["outcome"] == "draft", body
    async with res.factory() as session:
        draft = (
            await session.execute(
                select(AiMessage).where(
                    AiMessage.conversation_id == uuid.UUID(body["conversationId"]),
                    AiMessage.review == "pending",
                )
            )
        ).scalar_one()
        return body["conversationId"], draft.id


async def _me_id(client: AsyncClient, ctx: Ctx) -> str:
    me = await client.get(f"{API}/auth/me", headers=ctx.owner_headers)
    assert me.status_code == 200, me.text
    return str(me.json()["account"]["id"])


async def _counters(client: AsyncClient, ctx: Ctx) -> Any:
    return (
        await client.get(f"{API}/clinic/conversations/counters", headers=ctx.owner_headers)
    ).json()


async def _messages(client: AsyncClient, ctx: Ctx, conversation_id: str) -> list[Any]:
    resp = await client.get(
        f"{API}/clinic/conversations/{conversation_id}/messages", headers=ctx.owner_headers
    )
    assert resp.status_code == 200, resp.text
    return list(resp.json()["items"])


# ── Черновик появляется ─────────────────────────────────────────────────────


async def test_assisted_mode_makes_a_pending_draft(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "assisted")
    conv_id, draft_id = await _draft(
        admin_client, ctx, fake, _resources, "+996700200300", "Почём чистка?"
    )

    bot = next(m for m in await _messages(client, ctx, conv_id) if m["role"] == "bot")
    assert bot["review"] == "pending" and bot["sentAt"] is None
    assert bot["id"] == draft_id

    counters = await _counters(client, ctx)
    assert counters["needsHuman"] == 1
    listed = (await client.get(f"{API}/clinic/conversations", headers=ctx.owner_headers)).json()
    assert listed["list"][0]["pendingDraftId"] == draft_id
    assert listed["list"][0]["needsHuman"] is True

    async with _resources.factory() as session:
        events = [e.type for e in (await session.execute(select(Event))).scalars()]
    assert "ai.draft_ready" in events  # администратора позовёт диспетчер Ф3


# ── Решения по черновику ────────────────────────────────────────────────────


async def test_send_draft_as_is(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "assisted")
    conv_id, draft_id = await _draft(
        admin_client, ctx, fake, _resources, "+996700200301", "Почём чистка?"
    )

    sent = await client.post(
        f"{API}/clinic/conversations/{conv_id}/drafts/{draft_id}/send", headers=ctx.owner_headers
    )
    assert sent.status_code == 201, sent.text
    payload = sent.json()
    assert payload["review"] == "approved"
    assert payload["sentAt"] is not None
    assert payload["reviewedBy"] == await _me_id(client, ctx)
    assert payload["body"] == DRAFT_TEXT and payload["originalBody"] is None
    assert (await _counters(client, ctx))["needsHuman"] == 0

    again = await client.post(
        f"{API}/clinic/conversations/{conv_id}/drafts/{draft_id}/send", headers=ctx.owner_headers
    )
    assert again.status_code == 409 and again.json()["error"] == "draft_not_pending"


async def test_send_draft_with_edit_keeps_original(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "assisted")
    conv_id, draft_id = await _draft(
        admin_client, ctx, fake, _resources, "+996700200302", "Почём чистка?"
    )

    sent = await client.post(
        f"{API}/clinic/conversations/{conv_id}/drafts/{draft_id}/send",
        headers=ctx.owner_headers,
        json={"body": "Здравствуйте! Чистка — 2 500 сом, завтра есть 11:00."},
    )
    assert sent.status_code == 201, sent.text
    assert sent.json()["review"] == "edited"
    assert sent.json()["originalBody"] == DRAFT_TEXT  # сигнал для обучения (Ф6)
    assert sent.json()["body"].startswith("Здравствуйте! Чистка — 2 500")


async def test_discard_draft(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "assisted")
    conv_id, draft_id = await _draft(
        admin_client, ctx, fake, _resources, "+996700200303", "Почём чистка?"
    )

    resp = await client.post(
        f"{API}/clinic/conversations/{conv_id}/drafts/{draft_id}/discard", headers=ctx.owner_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["review"] == "discarded" and resp.json()["sentAt"] is None
    assert (await _counters(client, ctx))["needsHuman"] == 0


# ── Устаревание ─────────────────────────────────────────────────────────────


async def test_new_patient_message_supersedes_old_draft(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "assisted")
    phone = "+996700200304"
    conv_id, old_id = await _draft(admin_client, ctx, fake, _resources, phone, "Почём чистка?")
    _, new_id = await _draft(admin_client, ctx, fake, _resources, phone, "И когда можно?")
    assert new_id != old_id

    reviews = {
        m["id"]: m["review"] for m in await _messages(client, ctx, conv_id) if m["role"] == "bot"
    }
    assert reviews[old_id] == "superseded" and reviews[new_id] == "pending"
    stale = await client.post(
        f"{API}/clinic/conversations/{conv_id}/drafts/{old_id}/send", headers=ctx.owner_headers
    )
    assert stale.status_code == 409 and stale.json()["error"] == "draft_not_pending"


async def test_draft_is_stale_once_patient_writes_again(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Пациент дописал, а бот ещё не успел с новым черновиком — старый не уходит."""
    await _set_mode(admin_client, ctx, "assisted")
    phone = "+996700200305"
    conv_id, draft_id = await _draft(admin_client, ctx, fake, _resources, phone, "Почём чистка?")
    # Вебхук без очереди сохраняет сообщение, но ассистента не зовёт.
    await _webhook(client, _inbound(ctx.phone_number_id, phone, "Или лучше отбеливание?"))

    resp = await client.post(
        f"{API}/clinic/conversations/{conv_id}/drafts/{draft_id}/send", headers=ctx.owner_headers
    )
    assert resp.status_code == 409 and resp.json()["error"] == "draft_stale"


async def test_closed_window_blocks_draft_send(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "assisted")
    conv_id, draft_id = await _draft(
        admin_client, ctx, fake, _resources, "+996700200306", "Почём чистка?"
    )
    async with _resources.factory() as session:
        conv = await session.get(AiConversation, uuid.UUID(conv_id))
        assert conv is not None
        conv.last_patient_msg_at = datetime.now(UTC) - timedelta(days=2)
        await session.commit()

    resp = await client.post(
        f"{API}/clinic/conversations/{conv_id}/drafts/{draft_id}/send", headers=ctx.owner_headers
    )
    assert resp.status_code == 422 and resp.json()["error"] == "window_closed"


# ── Кто ведёт диалог ────────────────────────────────────────────────────────


async def test_takeover_silences_bot_and_return_resumes(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "assisted")
    phone = "+996700200307"
    conv_id, _ = await _draft(admin_client, ctx, fake, _resources, phone, "Почём чистка?")

    taken = await client.post(
        f"{API}/clinic/conversations/{conv_id}/assistant",
        headers=ctx.owner_headers,
        json={"active": False},
    )
    assert taken.status_code == 200, taken.text
    assert taken.json() == {
        "conversationId": conv_id,
        "status": "escalated",
        "botActive": False,
        "resolvedEscalations": 0,
    }
    fake.script = [AiResult(text=_reply_json("Этого ответа быть не должно."))]
    silent = await _simulate(admin_client, ctx, "А отбеливание?", phone)
    assert silent["outcome"] == "skipped"  # диалог у человека — бот молчит

    returned = await client.post(
        f"{API}/clinic/conversations/{conv_id}/assistant",
        headers=ctx.owner_headers,
        json={"active": True},
    )
    assert returned.status_code == 200, returned.text
    assert returned.json()["botActive"] is True
    assert returned.json()["resolvedEscalations"] == 1
    async with _resources.factory() as session:
        escalation = (await session.execute(select(AiEscalation))).scalar_one()
        assert escalation.reason == "manual_takeover"
        assert escalation.resolved_by is not None and escalation.resolved_at is not None
        conv = await session.get(AiConversation, uuid.UUID(conv_id))
        assert conv is not None and conv.status is AiConversationStatus.ACTIVE

    fake.script = [AiResult(text=_reply_json("Отбеливание тоже делаем."))]
    back = await _simulate(admin_client, ctx, "Так что с отбеливанием?", phone)
    assert back["outcome"] == "draft"  # бот снова в деле
