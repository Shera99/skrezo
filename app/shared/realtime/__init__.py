"""Шов Ф3↔Ф4: интерфейс живой доставки на открытые экраны.

Диспетчер событий не знает про Socket.IO — он зовёт :class:`RealtimeGateway`.
До Ф4 живёт заглушка: инбокс наполняется, пуши уходят, а экран подхватывает
изменения 30-секундным фолбэком клиента (``LIVE_REFETCH_MS``). Ф4 заменит
реализацию рассылкой в комнату ``clinic:<id>`` — по сокету идут СИГНАЛЫ, а не
данные (клиент перечитывает REST сам).
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol


class RealtimeGateway(Protocol):
    """Разослать сигнал события всем открытым экранам клиники."""

    async def emit(self, clinic_id: uuid.UUID, event: dict[str, Any]) -> None: ...


class NoopRealtimeGateway:
    """Заглушка до Ф4: сигналы никуда не идут, всё остальное работает."""

    async def emit(self, clinic_id: uuid.UUID, event: dict[str, Any]) -> None:
        return None


def build_realtime_gateway() -> RealtimeGateway:
    """Реализация по конфигурации; пока Ф4 нет — всегда заглушка."""
    return NoopRealtimeGateway()
