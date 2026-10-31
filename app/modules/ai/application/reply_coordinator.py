"""Координатор ответа ассистента (Ф8): один диалог — один ответ за раз.

Ответ живёт отдельным заданием от приёма вебхука, и у такого задания три
опасности, которые закрываются именно здесь:

* **два задания на один диалог** — пациент прислал два сообщения подряд, оба
  поставили задание; без лока бот ответил бы дважды, причём первый раз — на
  недосказанную мысль. Лок по диалогу в Redis: второе задание откладывается
  и, когда дойдёт до дела, увидит, что диалог уже отвечен;
* **ответ на уже отвеченное** — повтор задания или отложенный ход не должны
  давать второго ответа. Правило внутри сервиса ответа: если после последнего
  сообщения пациента уже есть наш ход — отвечать нечего;
* **временно недоступная модель** — ход откладывается и повторяется, а не
  эскалируется; человека зовём только после ``max_defers`` попыток.

Лок — Redis ``SET NX EX``, а не advisory-lock Postgres намеренно: соединение с
базой на время вызова модели отпускается, и держать на нём лок было бы нельзя.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.ai.application.reply_service import ProcessAiReplyService, ReplyOutcome
from app.shared.ai import AiProvider
from app.shared.whatsapp_cloud import WhatsAppCloudClient

logger = structlog.get_logger(__name__)

#: Постановка отложенного задания: (диалог, номер попытки).
Enqueue = Callable[[uuid.UUID, int], Awaitable[None]]


class ConversationLock:
    """Лок по диалогу: ``SET NX EX`` с токеном владельца."""

    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _key(conversation_id: uuid.UUID) -> str:
        return f"ai:reply:lock:{conversation_id}"

    async def acquire(self, conversation_id: uuid.UUID) -> str | None:
        """Токен владельца либо ``None`` — диалог занят другим заданием."""
        token = uuid.uuid4().hex
        taken = await self._redis.set(self._key(conversation_id), token, nx=True, ex=self._ttl)
        return token if taken else None

    async def release(self, conversation_id: uuid.UUID, token: str) -> None:
        """Снять только свой лок: чужой (после истечения TTL) не трогаем."""
        key = self._key(conversation_id)
        current: Any = await self._redis.get(key)
        if isinstance(current, bytes):
            current = current.decode()
        if current == token:
            await self._redis.delete(key)


@dataclass(frozen=True, slots=True)
class CoordinatorResult:
    """Чем кончилось задание ответа."""

    status: str  # статус ReplyOutcome (sent, held, …) | dropped | given_up
    outcome: ReplyOutcome | None = None


class ReplyCoordinator:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        provider_factory: Callable[[], AiProvider | None],
        wa_client_factory: Callable[[], WhatsAppCloudClient],
        lock: ConversationLock | None,
        enqueue: Enqueue | None,
        max_defers: int,
    ) -> None:
        self._session_factory = session_factory
        self._provider_factory = provider_factory
        self._wa_client_factory = wa_client_factory
        self._lock = lock
        self._enqueue = enqueue
        self._max_defers = max_defers

    async def run(self, conversation_id: uuid.UUID, defer_count: int = 0) -> CoordinatorResult:
        """Ответить на диалог, если он свободен и модель доступна; иначе отложить."""
        token: str | None = None
        if self._lock is not None:
            token = await self._lock.acquire(conversation_id)
            if token is None:
                # Другое задание уже отвечает на этот диалог: оно видит все
                # сообщения, пришедшие до его хода. Наше — подождёт и проверит.
                return await self._postpone(conversation_id, defer_count, "busy")
        try:
            async with self._session_factory() as session:
                service = ProcessAiReplyService(
                    session, self._provider_factory(), self._wa_client_factory()
                )
                outcome = await service.execute(conversation_id)
                if outcome.status != "deferred":
                    return CoordinatorResult(outcome.status, outcome)
                if defer_count >= self._max_defers:
                    # Модель не ожила за все попытки — честно зовём человека.
                    outcome = await service.give_up(
                        conversation_id,
                        f"модель недоступна после {defer_count} отложенных попыток",
                    )
                    return CoordinatorResult("given_up", outcome)
            return await self._postpone(conversation_id, defer_count, "provider_unavailable")
        finally:
            if token is not None and self._lock is not None:
                await self._lock.release(conversation_id, token)

    async def _postpone(
        self, conversation_id: uuid.UUID, defer_count: int, reason: str
    ) -> CoordinatorResult:
        if self._enqueue is None or defer_count >= self._max_defers:
            # Занятый диалог после всех попыток можно отпустить: у его ответа
            # уже был владелец, а новое сообщение поставит новое задание.
            logger.warning(
                "ai_reply_dropped",
                conversation_id=str(conversation_id),
                reason=reason,
                defers=defer_count,
            )
            return CoordinatorResult("dropped")
        await self._enqueue(conversation_id, defer_count + 1)
        logger.info(
            "ai_reply_deferred",
            conversation_id=str(conversation_id),
            reason=reason,
            defers=defer_count + 1,
        )
        return CoordinatorResult("deferred")
