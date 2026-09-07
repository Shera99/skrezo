"""Кэш префикса промпта у Gemini (Ф8).

Сеть не зовётся: ``httpx.MockTransport`` играет за Google, fakeredis — за
общий реестр кэшей. Проверяется контракт: кэш создаётся один раз на
содержимое, ходы ссылаются на него и не шлют промпт заново, короткий
префикс помнится как «без кэша», пропавший кэш не роняет ход.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fakeredis import aioredis

from app.shared.ai import AiMessagePart, AiToolDecl
from app.shared.ai.gemini import GeminiProvider

SYSTEM = "Ты — ассистент клиники. " * 200  # длинный префикс, как в бою
TOOLS = [AiToolDecl(name="get_services", description="Услуги", parameters={"type": "object"})]


def _answer(text: str = '{"reply": "ok"}', cached: int = 0) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {
            "promptTokenCount": 3000,
            "candidatesTokenCount": 20,
            "cachedContentTokenCount": cached,
        },
    }


class _Google:
    """Заглушка двух ручек: cachedContents и generateContent."""

    def __init__(self, *, create_status: int = 200, reject_cache: bool = False) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.create_status = create_status
        self.reject_cache = reject_cache
        self.created = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode() or "{}")
        self.requests.append((request.url.path, body))
        if request.url.path.endswith("/cachedContents"):
            self.created += 1
            if self.create_status != 200:
                return httpx.Response(
                    self.create_status,
                    json={"error": {"message": "Cached content is too small. minimum 1024 tokens"}},
                )
            return httpx.Response(
                200,
                json={
                    "name": f"cachedContents/c{self.created}",
                    "usageMetadata": {"totalTokenCount": 2800},
                },
            )
        if body.get("cachedContent") and self.reject_cache:
            return httpx.Response(
                400, json={"error": {"message": "CachedContent not found or expired"}}
            )
        cached = 2800 if body.get("cachedContent") else 0
        return httpx.Response(200, json=_answer(cached=cached))


def _provider(google: _Google, redis: Any, **kwargs: Any) -> GeminiProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(google.handler))
    return GeminiProvider("key", "gemini-test", client=client, redis=redis, **kwargs)


async def _ask(provider: GeminiProvider, scope: str | None = "clinic:1") -> Any:
    return await provider.complete(
        system=SYSTEM,
        parts=[AiMessagePart(role="user", text="Сколько стоит чистка?")],
        tools=TOOLS,
        temperature=0.2,
        max_tokens=256,
        cache_scope=scope,
    )


@pytest.mark.asyncio
async def test_cache_created_once_and_reused_by_following_turns() -> None:
    google, redis = _Google(), aioredis.FakeRedis()
    provider = _provider(google, redis, cache_ttl_seconds=1800)

    first = await _ask(provider)
    second = await _ask(provider)

    assert google.created == 1
    paths = [path for path, _ in google.requests]
    assert paths == ["/v1beta/cachedContents", "/v1beta/models/gemini-test:generateContent"] * 1 + [
        "/v1beta/models/gemini-test:generateContent"
    ]
    for _, body in google.requests[1:]:
        assert body["cachedContent"] == "cachedContents/c1"
        assert "system_instruction" not in body and "tools" not in body
    create_body = google.requests[0][1]
    assert create_body["systemInstruction"]["parts"][0]["text"] == SYSTEM
    assert create_body["tools"][0]["function_declarations"][0]["name"] == "get_services"
    assert create_body["ttl"] == "1800s"
    assert first.usage["cached"] == 2800 and second.usage["cached"] == 2800
    keys = await redis.keys("ai:prefix:clinic:1:*")
    assert len(keys) == 1 and 0 < await redis.ttl(keys[0]) <= 1740


@pytest.mark.asyncio
async def test_changed_prompt_or_other_clinic_gets_own_cache() -> None:
    google, redis = _Google(), aioredis.FakeRedis()
    provider = _provider(google, redis)
    await _ask(provider, "clinic:1")
    await _ask(provider, "clinic:2")
    await provider.complete(
        system=SYSTEM + "\nНОВЫЙ ДОКУМЕНТ БАЗЫ ЗНАНИЙ",
        parts=[AiMessagePart(role="user", text="?")],
        tools=TOOLS,
        temperature=0.2,
        max_tokens=256,
        cache_scope="clinic:1",
    )
    assert google.created == 3


@pytest.mark.asyncio
async def test_too_small_prefix_is_remembered_and_turns_go_without_cache() -> None:
    google, redis = _Google(create_status=400), aioredis.FakeRedis()
    provider = _provider(google, redis)

    result = await _ask(provider)
    await _ask(provider)

    assert google.created == 1  # второй ход не пытается снова
    turns = [body for path, body in google.requests if path.endswith(":generateContent")]
    assert len(turns) == 2 and all("system_instruction" in b and "tools" in b for b in turns)
    assert result.usage["cached"] == 0
    assert len(await redis.keys("ai:prefix:*:skip")) == 1  # «короткий префикс» помнится


@pytest.mark.asyncio
async def test_lost_cache_is_forgotten_and_turn_retried_without_it() -> None:
    google, redis = _Google(), aioredis.FakeRedis()
    provider = _provider(google, redis)
    await _ask(provider)  # кэш создан и записан
    google.reject_cache = True

    result = await _ask(provider)
    assert result.text == '{"reply": "ok"}'
    turns = [body for path, body in google.requests if path.endswith(":generateContent")]
    assert "cachedContent" in turns[-2] and "system_instruction" in turns[-1]
    assert await redis.keys("ai:prefix:clinic:1:*") == []  # реестр забыл пропавший кэш


@pytest.mark.asyncio
async def test_no_scope_or_no_redis_means_plain_request() -> None:
    google = _Google()
    without_redis = _provider(google, None)
    await _ask(without_redis)
    with_redis = _provider(google, aioredis.FakeRedis())
    await _ask(with_redis, scope=None)
    disabled = _provider(google, aioredis.FakeRedis(), cache_enabled=False)
    await _ask(disabled)

    assert google.created == 0
    assert all(path.endswith(":generateContent") for path, _ in google.requests)
    assert all("system_instruction" in body for _, body in google.requests)
