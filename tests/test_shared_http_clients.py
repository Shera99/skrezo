"""Ф8: один httpx-клиент на процесс — у Meta и Google, а не на вызов."""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.shared.http import close_shared_clients, shared_async_client
from app.shared.whatsapp_cloud.factory import build_whatsapp_client, reset_whatsapp_client
from app.shared.whatsapp_cloud.graph_client import GraphWhatsAppClient
from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient


@pytest.mark.asyncio
async def test_shared_client_is_reused_and_recreated_after_close() -> None:
    first = shared_async_client("probe", timeout=1.0)
    assert shared_async_client("probe", timeout=1.0) is first
    assert shared_async_client("other", timeout=1.0) is not first
    await close_shared_clients()
    assert first.is_closed
    assert shared_async_client("probe", timeout=1.0) is not first
    await close_shared_clients()


def test_whatsapp_client_is_one_per_process_and_follows_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_whatsapp_client()
    monkeypatch.setattr(settings, "wa_cloud_access_token", "")
    fallback = build_whatsapp_client()
    assert isinstance(fallback, LoggingWhatsAppClient)
    assert build_whatsapp_client() is fallback

    monkeypatch.setattr(settings, "wa_cloud_access_token", "token-1")
    live = build_whatsapp_client()
    assert isinstance(live, GraphWhatsAppClient) and build_whatsapp_client() is live
    monkeypatch.setattr(settings, "wa_cloud_access_token", "token-2")
    assert build_whatsapp_client() is not live  # ротация токена пересобирает клиент
    reset_whatsapp_client()


@pytest.mark.asyncio
async def test_graph_client_sends_everything_through_the_injected_client() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
        return httpx.Response(200, json={"id": "123", "data": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    graph = GraphWhatsAppClient("https://graph.test/v23.0", "token", client=client)
    sent = await graph.send_text("123", "+996700000000", "Привет")
    assert sent.wa_message_id == "wamid.1"
    assert await graph.check_token("123") is True
    assert await graph.fetch_templates("waba") == []
    assert seen == [
        "POST /v23.0/123/messages",
        "GET /v23.0/123",
        "GET /v23.0/waba/message_templates",
    ]
    await client.aclose()
    assert json.dumps(seen)  # список сериализуем — просто на всякий случай
