"""Реалтайм-шина: сигналы «обновись» для открытых экранов (Ф4, ТЗ-СУ §9).

По сокету идут СИГНАЛЫ, а не данные: клиент инвалидирует ключи TanStack Query
и перечитывает REST — потерянный сигнал даёт задержку, а не неверные данные.

Шина между процессами — ``pg_notify`` (канал ``realtime``): диспетчер живёт в
воркере, ручки — в API-процессах, комнаты Socket.IO — в памяти каждого
API-процесса. Публикация идёт В ТРАНЗАКЦИИ (``NOTIFY`` срабатывает на
коммите — сигнал об откате разослать невозможно), слушает её каждый процесс
шлюза (``app/realtime``). Слушателей нет — сигнал исчезает, и это нормально:
клиент дотянет 30-секундным фолбэком.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

#: Канал pg_notify, который слушают процессы Socket.IO-шлюза.
REALTIME_CHANNEL = "realtime"


class RealtimeGateway(Protocol):
    """Разослать сигнал события всем открытым экранам клиники."""

    async def emit(self, clinic_id: uuid.UUID, type_: str, payload: dict[str, Any]) -> None: ...


class NoopRealtimeGateway:
    """Заглушка для тестов: сигналы никуда не идут, всё остальное работает."""

    async def emit(self, clinic_id: uuid.UUID, type_: str, payload: dict[str, Any]) -> None:
        return None


async def publish_realtime(
    session: AsyncSession, clinic_id: uuid.UUID, type_: str, payload: dict[str, Any]
) -> None:
    """Опубликовать сигнал в шину — из ТЕКУЩЕЙ транзакции, до ``commit()``.

    Зовут и диспетчер (доменные события), и ручки (например, ``inbox.read`` —
    синхронизация бейджа между устройствами): у сигнальных событий нет строки
    в outbox, шина для них — единственный путь на экран.
    """
    message = json.dumps(
        {"clinicId": str(clinic_id), "type": type_, "payload": payload},
        ensure_ascii=False,
    )
    await session.execute(select(func.pg_notify(REALTIME_CHANNEL, message)))


class PgNotifyRealtimeGateway:
    """Гейтвей поверх шины: то, чем диспетчер шлёт сигналы (шов Ф3↔Ф4 закрыт)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def emit(self, clinic_id: uuid.UUID, type_: str, payload: dict[str, Any]) -> None:
        await publish_realtime(self._session, clinic_id, type_, payload)


def build_realtime_gateway(session: AsyncSession) -> RealtimeGateway:
    """Рабочий гейтвей шины; в тестах диспетчеру передают Noop напрямую."""
    return PgNotifyRealtimeGateway(session)
