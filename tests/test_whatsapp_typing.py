"""«Печатает…» через Graph API: сбой индикатора не роняет и не задерживает ход ассистента."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.shared.whatsapp_cloud import graph_client
from app.shared.whatsapp_cloud.graph_client import GraphWhatsAppClient


def _client(handler: httpx.MockTransport) -> GraphWhatsAppClient:
    return GraphWhatsAppClient(
        "https://graph.test/v21.0", "token", client=httpx.AsyncClient(transport=handler)
    )


async def test_typing_marks_message_read_with_indicator() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"success": True})

    await _client(httpx.MockTransport(handler)).send_typing("123", "wamid.IN")

    [request] = sent
    assert str(request.url) == "https://graph.test/v21.0/123/messages"
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.IN",
        "typing_indicator": {"type": "text"},
    }


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(500, json={"error": {"message": "boom"}}), httpx.ConnectError("down")],
    ids=["graph_500", "network"],
)
async def test_typing_failure_is_swallowed(failure: httpx.Response | Exception) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(failure, Exception):
            raise failure
        return failure

    await _client(httpx.MockTransport(handler)).send_typing("123", "wamid.IN")  # без исключения


async def test_slow_graph_does_not_hold_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Потолок — на весь запрос: медленный Graph не держит ход дольше него."""
    monkeypatch.setattr(graph_client, "_TYPING_TIMEOUT", 0.05)

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json={"success": True})

    loop = asyncio.get_running_loop()
    started = loop.time()
    await _client(httpx.MockTransport(handler)).send_typing("123", "wamid.IN")

    assert loop.time() - started < 1
