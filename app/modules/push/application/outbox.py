"""Запись доменного события в outbox — В ТОЙ ЖЕ транзакции, что изменение.

Единственная точка входа для эмиссии (шов Ф2/Ф3: ассистент будет писать сюда
же). Отдельным запросом после коммита писать нельзя — получится событие о том,
чего не произошло; поэтому вызывается из use-case ДО ``commit()``, и откат
транзакции забирает событие вместе с изменением.

``pg_notify`` здесь — только будильник диспетчера: он срабатывает на коммите
(событие об откате разослать невозможно), ничего не хранит, и его потерю
страхует 30-секундный поллинг воркера.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.db.models.event import Event

#: Канал pg_notify, который слушает диспетчер воркера.
NOTIFY_CHANNEL = "events"


async def emit_event(
    session: AsyncSession,
    *,
    clinic_id: uuid.UUID,
    type_: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    """Положить событие в outbox текущей транзакции и разбудить диспетчера."""
    event = Event(
        clinic_id=clinic_id,
        type=type_,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload or {},
    )
    session.add(event)
    await session.flush()
    await session.execute(select(func.pg_notify(NOTIFY_CHANNEL, str(event.id))))
    return event
