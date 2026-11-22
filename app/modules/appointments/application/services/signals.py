"""Сигнал открытым экранам «приём изменился» — для правок мимо outbox (Ф4).

Смена статуса, перенос и отмена идут доменными событиями: их сигнал шлёт
диспетчер. Но у приёма есть и то, что событием каталога не является, а на
экране видно: состояние напоминания пациенту (``reminder`` в ответе приёма).
Такие правки зовут этот сигнал сами — тем же клиентским именем
``appointment.updated``, которое кабинет и приложение уже разбирают: по нему
они перечитывают приём, данных в сигнале нет.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.realtime import publish_realtime


async def signal_appointment_updated(
    session: AsyncSession,
    clinic_id: uuid.UUID,
    appointment_id: uuid.UUID,
    fields: Sequence[str],
) -> None:
    """Опубликовать ``appointment.updated`` из ТЕКУЩЕЙ транзакции, до ``commit()``.

    :param fields: изменившиеся поля ответа приёма в camelCase, например
        ``["reminder"]`` — подсказка клиенту, что именно перечитывать.
    """
    await publish_realtime(
        session,
        clinic_id,
        "appointment.updated",
        {"id": str(appointment_id), "fields": list(fields)},
    )
