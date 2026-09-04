"""Ф5, инкремент 1: список чатов кабинета ассистента.

Всё, чем живёт левая колонка макета: синяя точка непрочитанного и бейдж
раздела, вкладки «Все / Ждут ответа / Нужен человек» со счётчиками, поиск по
имени, номеру и тексту, метка уверенности ответа, превью голосового с
длительностью.
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient

from app.main import app
from app.modules.whatsapp.presentation.deps import get_whatsapp_client
from app.shared.ai import AiResult
from app.shared.whatsapp_cloud.client import DownloadedMedia
from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient
from tests.api.conftest import (
    Ctx,
    _FakeProvider,
    _inbound,
    _reply_json,
    _set_mode,
    _simulate,
    _webhook,
)
from tests.conftest import API
from tests.test_media_duration import wav_bytes


async def _list(client: AsyncClient, ctx: Ctx, **params: Any) -> Any:
    resp = await client.get(f"{API}/clinic/conversations", headers=ctx.owner_headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _counters(client: AsyncClient, ctx: Ctx) -> Any:
    resp = await client.get(f"{API}/clinic/conversations/counters", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Непрочитанное и бейдж ───────────────────────────────────────────────────


async def test_unread_dot_and_badge_clear_on_read(client: AsyncClient, ctx: Ctx) -> None:
    phone = "+996700200200"
    await _webhook(client, _inbound(ctx.phone_number_id, phone, "Здравствуйте, вы работаете?"))

    item = (await _list(client, ctx))["list"][0]
    assert item["unreadCount"] == 1
    assert item["awaitingReply"] is True
    assert item["lastMessageRole"] == "patient"
    assert item["lastMessageKind"] == "text"
    counters = await _counters(client, ctx)
    assert (counters["unread"], counters["awaiting"], counters["total"]) == (1, 1, 1)

    read = await client.post(
        f"{API}/clinic/conversations/{item['id']}/read", headers=ctx.owner_headers
    )
    assert read.status_code == 200, read.text
    assert read.json()["unreadCount"] == 0
    assert read.json()["readMessageId"] is not None

    assert (await _list(client, ctx))["list"][0]["unreadCount"] == 0
    assert (await _counters(client, ctx))["unread"] == 0
    # Ждёт ответа по-прежнему: прочитать — не значит ответить.
    assert (await _counters(client, ctx))["awaiting"] == 1


async def test_read_mark_never_moves_backwards(client: AsyncClient, ctx: Ctx) -> None:
    phone = "+996700200210"
    await _webhook(client, _inbound(ctx.phone_number_id, phone, "раз"))
    await _webhook(client, _inbound(ctx.phone_number_id, phone, "два"))
    conv = (await _list(client, ctx))["list"][0]
    assert conv["unreadCount"] == 2

    latest = await client.post(
        f"{API}/clinic/conversations/{conv['id']}/read", headers=ctx.owner_headers
    )
    read_id = latest.json()["readMessageId"]
    # Позднее подтверждение «до первого сообщения» не возвращает точку.
    older = await client.post(
        f"{API}/clinic/conversations/{conv['id']}/read",
        headers=ctx.owner_headers,
        json={"upToMessageId": read_id - 1},
    )
    assert older.json()["readMessageId"] == read_id
    assert older.json()["unreadCount"] == 0


async def test_foreign_conversation_read_is_404(client: AsyncClient, ctx: Ctx) -> None:
    resp = await client.post(
        f"{API}/clinic/conversations/{uuid.uuid4()}/read", headers=ctx.owner_headers
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "conversation_not_found"


# ── Вкладки ─────────────────────────────────────────────────────────────────


async def test_awaiting_clears_after_clinic_reply(client: AsyncClient, ctx: Ctx) -> None:
    phone = "+996700200220"
    await _webhook(client, _inbound(ctx.phone_number_id, phone, "Можно записаться?"))
    conv = (await _list(client, ctx, filter="awaiting"))["list"]
    assert len(conv) == 1

    sent = await client.post(
        f"{API}/clinic/conversations/{conv[0]['id']}/messages",
        headers=ctx.owner_headers,
        json={"body": "Конечно! На какой день?"},
    )
    assert sent.status_code == 201, sent.text

    assert (await _list(client, ctx, filter="awaiting"))["list"] == []
    item = (await _list(client, ctx))["list"][0]
    assert item["awaitingReply"] is False
    assert item["lastMessageRole"] == "admin"
    assert (await _counters(client, ctx))["awaiting"] == 0


async def test_needs_human_after_escalation(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Это совсем не больно, приходите!"))]
    body = await _simulate(admin_client, ctx, "Больно ли лечить?", "+996700200230")
    assert body["outcome"] == "escalated"

    counters = await _counters(client, ctx)
    assert counters["needsHuman"] == 1
    needs = (await _list(client, ctx, filter="needs_human"))["list"]
    assert [c["id"] for c in needs] == [body["conversationId"]]
    assert needs[0]["needsHuman"] is True
    # Пациенту ушёл шаблон эскалации — последнее слово за клиникой, не «ждёт ответа».
    assert (await _list(client, ctx, filter="awaiting"))["list"] == []


# ── Поиск ───────────────────────────────────────────────────────────────────


async def test_search_by_text_phone_and_patient_name(client: AsyncClient, ctx: Ctx) -> None:
    created = await client.post(
        f"{API}/clinic/patients",
        headers=ctx.owner_headers,
        json={"phone": "0700200240", "firstName": "Гулмира", "lastName": "Токтосунова"},
    )
    assert created.status_code == 201, created.text
    await _webhook(
        client, _inbound(ctx.phone_number_id, "+996700200240", "Хочу перенести чистку на четверг")
    )
    await _webhook(client, _inbound(ctx.phone_number_id, "+996700200241", "Где вы находитесь?"))

    assert (await _list(client, ctx, q="четверг"))["totalCount"] == 1
    assert (await _list(client, ctx, q="Токтосун"))["totalCount"] == 1
    assert (await _list(client, ctx, q="700200241"))["totalCount"] == 1
    assert (await _list(client, ctx, q="ничего такого"))["totalCount"] == 0
    assert (await _list(client, ctx))["totalCount"] == 2


# ── Уверенность и медиа ─────────────────────────────────────────────────────


async def test_confidence_is_kept_on_bot_message(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, fake: _FakeProvider
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?", confidence="medium"))]
    body = await _simulate(admin_client, ctx, "Салам", "+996700200250")
    assert body["outcome"] == "sent"

    messages = await client.get(
        f"{API}/clinic/conversations/{body['conversationId']}/messages", headers=ctx.owner_headers
    )
    assert messages.status_code == 200, messages.text
    bot = next(m for m in messages.json()["items"] if m["role"] == "bot")
    assert bot["confidence"] == "medium"


class _MediaClient(LoggingWhatsAppClient):
    """Лог-клиент, который «скачивает» полуторасекундное голосовое."""

    async def fetch_media(self, media_id: str) -> DownloadedMedia | None:
        return DownloadedMedia(content=wav_bytes(1.5), mime="audio/wav")


async def test_voice_preview_carries_duration(client: AsyncClient, ctx: Ctx) -> None:
    app.dependency_overrides[get_whatsapp_client] = lambda: _MediaClient()
    try:
        await _webhook(client, _inbound(ctx.phone_number_id, "+996700200260", audio_id="media-1"))
    finally:
        app.dependency_overrides.pop(get_whatsapp_client, None)

    item = (await _list(client, ctx))["list"][0]
    assert item["lastMessageKind"] == "audio"
    assert item["lastMessageDurationSec"] == 2
    messages = await client.get(
        f"{API}/clinic/conversations/{item['id']}/messages", headers=ctx.owner_headers
    )
    voice = messages.json()["items"][0]
    assert voice["mediaType"] == "audio" and voice["hasMedia"] is True
    assert voice["mediaDurationSec"] == 2
