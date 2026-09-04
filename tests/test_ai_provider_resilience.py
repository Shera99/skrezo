"""Устойчивость провайдера модели (Ф8): классификация ошибок, повторы, предохранитель.

Сеть не зовётся: ``httpx.MockTransport`` играет за Google, fakeredis — за общий
Redis воркера. Проверяется наша логика: что временное, что окончательное, и
что после N сбоев подряд контур перестаёт долбить провайдера.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
from fakeredis import aioredis

from app.shared.ai.gemini import GeminiProvider
from app.shared.ai.provider import (
    AiMessagePart,
    AiProvider,
    AiProviderError,
    AiProviderUnavailableError,
    AiResult,
)
from app.shared.ai.resilience import FAILURES_KEY, OPEN_KEY, ResilientProvider


def _gemini(handler: object) -> GeminiProvider:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return GeminiProvider("key", "model", client=httpx.AsyncClient(transport=transport))


async def _call(provider: AiProvider) -> AiResult:
    return await provider.complete(
        system="s",
        parts=[AiMessagePart(role="user", text="привет")],
        tools=[],
        temperature=0.0,
        max_tokens=16,
    )


# ── Классификация ответов Gemini ────────────────────────────────────────────


async def test_throttling_is_temporary_and_carries_retry_after() -> None:
    """429 — временное: ход откладывается, а Retry-After провайдера уважается."""
    provider = _gemini(lambda _r: httpx.Response(429, headers={"Retry-After": "7"}, text="{}"))
    with pytest.raises(AiProviderUnavailableError) as raised:
        await _call(provider)
    assert raised.value.retry_after == 7.0


async def test_server_error_is_temporary() -> None:
    provider = _gemini(lambda _r: httpx.Response(503, text="overloaded"))
    with pytest.raises(AiProviderUnavailableError):
        await _call(provider)


async def test_bad_request_is_permanent() -> None:
    """400/401/403 повтором не лечатся — это обычная ошибка, не «недоступен»."""
    provider = _gemini(lambda _r: httpx.Response(400, text="bad"))
    with pytest.raises(AiProviderError) as raised:
        await _call(provider)
    assert not isinstance(raised.value, AiProviderUnavailableError)


async def test_network_failure_is_temporary() -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети")

    with pytest.raises(AiProviderUnavailableError):
        await _call(_gemini(boom))


# ── Повторы и предохранитель ────────────────────────────────────────────────


@dataclass
class _Flaky:
    """Провайдер по сценарию: список исключений/результатов на каждый вызов."""

    script: list[object]
    model_name: str = "flaky"
    calls: int = 0

    async def complete(self, **_: object) -> AiResult:
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, AiResult)
        return step


@dataclass
class _Sleeper:
    delays: list[float] = field(default_factory=list)

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _resilient(
    inner: _Flaky, redis: object = None, *, retries: int = 2, threshold: int = 3
) -> tuple[ResilientProvider, _Sleeper]:
    sleeper = _Sleeper()
    provider = ResilientProvider(
        inner,
        redis,  # type: ignore[arg-type]
        retries=retries,
        backoff_seconds=1.0,
        breaker_threshold=threshold,
        breaker_open_seconds=60,
        sleep=sleeper,
    )
    return provider, sleeper


async def test_retries_temporary_failure_then_succeeds() -> None:
    inner = _Flaky([AiProviderUnavailableError("503"), AiResult(text="ок", model="flaky")])
    provider, sleeper = _resilient(inner)
    result = await _call(provider)
    assert result.text == "ок"
    assert inner.calls == 2
    assert sleeper.delays == [1.0]  # первый бэкофф — база


async def test_retry_after_overrides_backoff() -> None:
    inner = _Flaky(
        [AiProviderUnavailableError("429", retry_after=5.0), AiResult(text="ок", model="flaky")]
    )
    provider, sleeper = _resilient(inner)
    await _call(provider)
    assert sleeper.delays == [5.0]


async def test_permanent_error_is_not_retried() -> None:
    inner = _Flaky([AiProviderError("400")])
    provider, sleeper = _resilient(inner)
    with pytest.raises(AiProviderError):
        await _call(provider)
    assert inner.calls == 1
    assert sleeper.delays == []


async def test_breaker_opens_after_consecutive_failures() -> None:
    """N сбоев подряд — цепь разомкнута: провайдера больше не дёргаем."""
    redis = aioredis.FakeRedis(decode_responses=True)
    inner = _Flaky([AiProviderUnavailableError("503")] * 10)
    provider, _ = _resilient(inner, redis, retries=0, threshold=2)

    for _ in range(2):
        with pytest.raises(AiProviderUnavailableError):
            await _call(provider)
    assert await redis.exists(OPEN_KEY)
    calls_before = inner.calls

    with pytest.raises(AiProviderUnavailableError):
        await _call(provider)
    assert inner.calls == calls_before  # отбито без вызова


async def test_success_resets_failure_streak() -> None:
    redis = aioredis.FakeRedis(decode_responses=True)
    inner = _Flaky([AiProviderUnavailableError("503"), AiResult(text="ок", model="flaky")])
    provider, _ = _resilient(inner, redis, retries=0, threshold=5)
    with pytest.raises(AiProviderUnavailableError):
        await _call(provider)
    assert await redis.get(FAILURES_KEY) == "1"
    await _call(provider)
    assert await redis.get(FAILURES_KEY) is None
