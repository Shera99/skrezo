"""Устойчивость к сбоям провайдера модели (Ф8): повторы и предохранитель.

Под нагрузкой единичный 429 или 503 от модели — норма, а не событие. Без этого
слоя каждый такой ответ становился эскалацией к человеку с critical-пушем, и
один троттлинг превращался в лавину: сто диалогов разом «требуют администратора».

Два механизма, оба не знают, какая модель внутри:

* **повторы** — только на :class:`AiProviderUnavailableError` (429, 5xx, сеть);
  бэкофф экспоненциальный, ``Retry-After`` от провайдера уважается;
* **предохранитель** — N сбоев подряд размыкают цепь на T секунд: вызовы
  отбиваются мгновенно, задания ответа откладываются, а не сыплют эскалациями.
  Состояние живёт в Redis — общее для всех процессов воркера; без Redis
  предохранителя нет, повторы работают.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.shared.ai.provider import (
    AiMessagePart,
    AiProvider,
    AiProviderUnavailableError,
    AiResult,
    AiToolDecl,
)

logger = structlog.get_logger(__name__)

FAILURES_KEY = "ai:provider:failures"
OPEN_KEY = "ai:provider:open"


class ResilientProvider:
    """Обёртка над любым :class:`AiProvider`: повторы + предохранитель."""

    def __init__(
        self,
        inner: AiProvider,
        redis: Redis | None = None,
        *,
        retries: int,
        backoff_seconds: float,
        breaker_threshold: int,
        breaker_open_seconds: int,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._redis = redis
        self._retries = max(0, retries)
        self._backoff = backoff_seconds
        self._threshold = max(1, breaker_threshold)
        self._open_seconds = breaker_open_seconds
        self._sleep = sleep
        self.model_name = inner.model_name

    async def complete(
        self,
        *,
        system: str,
        parts: list[AiMessagePart],
        tools: list[AiToolDecl],
        temperature: float,
        max_tokens: int,
    ) -> AiResult:
        if await self._is_open():
            raise AiProviderUnavailableError("провайдер на паузе: предохранитель разомкнут")
        for attempt in range(self._retries + 1):
            try:
                result = await self._inner.complete(
                    system=system,
                    parts=parts,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except AiProviderUnavailableError as exc:
                if attempt < self._retries:
                    # Retry-After провайдера точнее нашей прикидки — уважаем.
                    delay = exc.retry_after or self._backoff * (2**attempt)
                    logger.info("ai_provider_retry", attempt=attempt + 1, delay=delay)
                    await self._sleep(delay)
                    continue
                await self._record_failure()
                raise
            await self._record_success()
            return result
        raise AiProviderUnavailableError("провайдер не ответил")  # недостижимо

    # ── Предохранитель (best-effort: сбой Redis его не роняет) ──────────────

    async def _is_open(self) -> bool:
        if self._redis is None:
            return False
        try:
            return bool(await self._redis.exists(OPEN_KEY))
        except RedisError as exc:
            logger.warning("ai_breaker_redis_failed", error=str(exc))
            return False

    async def _record_failure(self) -> None:
        if self._redis is None:
            return
        try:
            failures = int(await self._redis.incr(FAILURES_KEY))
            await self._redis.expire(FAILURES_KEY, self._open_seconds * 2)
            if failures >= self._threshold:
                await self._redis.set(OPEN_KEY, "1", ex=self._open_seconds)
                await self._redis.delete(FAILURES_KEY)
                logger.error("ai_breaker_opened", failures=failures, seconds=self._open_seconds)
        except RedisError as exc:
            logger.warning("ai_breaker_redis_failed", error=str(exc))

    async def _record_success(self) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.delete(FAILURES_KEY)
        except RedisError as exc:
            logger.warning("ai_breaker_redis_failed", error=str(exc))
