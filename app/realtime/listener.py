"""Слушатель шины ``realtime``: pg_notify → комнаты Socket.IO этого процесса.

Комнаты живут в памяти каждого API-процесса: админ на инстансе A, событие
родилось на B — без шины врач не получил бы ничего, и отказ был бы тихим и
плавающим (ловушка №2 задания Ф4). Шина — тот же ``NOTIFY``, что будит
диспетчер: долговечность не нужна, потому что по сокету идут только сигналы,
а потерю покрывает 30-секундный фолбэк клиента.

``LISTEN`` требует прямого соединения: пулер в transaction-режиме его не
переживает (ловушка №3) — поэтому asyncpg, мимо SQLAlchemy-пула.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import socketio
import structlog

from app.core.config import settings
from app.realtime.server import room_of
from app.shared.realtime import REALTIME_CHANNEL

logger = structlog.get_logger(__name__)


def _plain_dsn() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def _forward(sio: socketio.AsyncServer, raw: str) -> None:
    """Разобрать сигнал шины и разослать в комнату клиники (fire-and-forget)."""
    try:
        message = json.loads(raw)
        clinic_id = message["clinicId"]
        type_ = message["type"]
        payload = message.get("payload") or {}
    except (ValueError, KeyError) as exc:
        logger.warning("realtime_bad_signal", error=str(exc))
        return
    # Задачей, а не await: колбэк asyncpg синхронный, а emit — корутина.
    asyncio.get_running_loop().create_task(
        sio.emit(type_, payload, room=room_of(clinic_id))
    )


async def run_realtime_listener(sio: socketio.AsyncServer) -> None:
    """Держать LISTEN-соединение; падает — переподключаемся, шлюз живёт."""
    while True:
        try:
            conn = await asyncpg.connect(_plain_dsn())
            try:

                def _on_notify(
                    _conn: Any, _pid: Any, _channel: Any, payload: str
                ) -> None:
                    _forward(sio, payload)

                await conn.add_listener(REALTIME_CHANNEL, _on_notify)
                logger.info("realtime_listener_connected")
                while True:
                    await asyncio.sleep(60)
                    await conn.execute("SELECT 1")
            finally:
                await conn.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # соединение — расходник, слушатель — нет
            logger.warning("realtime_listener_reconnect", error=str(exc))
            await asyncio.sleep(5)
