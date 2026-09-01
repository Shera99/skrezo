"""Цикл диспетчера событий в воркере: LISTEN + страховочный поллинг (ТЗ-СУ §1).

Живёт фоновой задачей внутри arq-воркера (``on_startup``): отдельный процесс
не нужен, а рестарт воркера автоматически перезапускает и цикл.

``NOTIFY`` — будильник, не шина: он ничего не хранит, и слушателя нет во время
деплоя. Поэтому истина — строки ``event``, а цикл просыпается ЛИБО по сигналу,
ЛИБО раз в 30 секунд: потерянный сигнал задерживает доставку, но не теряет её.

Слушатель требует прямого соединения (LISTEN не переживает пулер в
transaction-режиме — ловушка из ТЗ §9); если соединение не поднимается,
цикл честно живёт на одном поллинге.
"""

from __future__ import annotations

import asyncio
import contextlib

import asyncpg
import structlog

from app.core.config import settings
from app.core.db import session_factory
from app.modules.push.application.event_dispatcher import EventDispatcher
from app.modules.push.application.outbox import NOTIFY_CHANNEL
from app.shared.push.factory import build_push_transport
from app.shared.realtime import build_realtime_gateway

logger = structlog.get_logger(__name__)

#: Страховочный интервал поллинга outbox (ТЗ-СУ §14).
POLL_INTERVAL_SECONDS = 30.0


def _plain_dsn() -> str:
    """DSN для asyncpg без драйверного префикса SQLAlchemy."""
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def _listen_notify(wakeup: asyncio.Event) -> None:
    """Держать LISTEN-соединение и будить цикл на каждый ``NOTIFY``.

    Соединение падает — переподключаемся с паузой; совсем не поднимается —
    просто спим и пробуем снова: поллинг основного цикла всё равно доставит.
    """
    while True:
        try:
            conn = await asyncpg.connect(_plain_dsn())
            try:
                await conn.add_listener(
                    NOTIFY_CHANNEL, lambda *_args: wakeup.set()
                )
                logger.info("event_listener_connected")
                # Соединение живёт молча; проверяем его раз в минуту пингом.
                while True:
                    await asyncio.sleep(60)
                    await conn.execute("SELECT 1")
            finally:
                await conn.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # соединение — расходник, цикл — нет
            logger.warning("event_listener_reconnect", error=str(exc))
            await asyncio.sleep(5)


async def run_dispatcher_loop() -> None:
    """Бесконечный цикл: разобрать всё накопленное → уснуть до сигнала/таймаута."""
    wakeup = asyncio.Event()
    listener = asyncio.create_task(_listen_notify(wakeup))
    logger.info("event_dispatcher_started")
    try:
        while True:
            try:
                async with session_factory() as session:
                    dispatcher = EventDispatcher(
                        session, build_push_transport(), build_realtime_gateway()
                    )
                    processed = await dispatcher.dispatch_pending()
            except Exception as exc:  # БД мигнула — цикл живёт дальше
                logger.error("event_dispatcher_tick_failed", error=str(exc))
                processed = 0
            if processed:
                continue  # в очереди могло остаться больше пачки — сразу ещё раз
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wakeup.wait(), timeout=POLL_INTERVAL_SECONDS)
            wakeup.clear()
    finally:
        listener.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await listener
