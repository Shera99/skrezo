"""Задача рассылки пуша: событие → устройства клиники.

Ставится по событию из обработчика запроса (сейчас — ``sync.day`` при изменении
расписания). Повторяется при недоставке: провайдер (FCM/APNs) мог просто не
ответить. Каждый запуск виден в журнале ``job_run``.

Диспетчер держим здесь тонким адаптером: сессия и транспорт создаются на задачу,
а вся логика — в :class:`app.modules.push.application.dispatcher.PushDispatcher`,
чтобы её можно было вызвать и из теста, не поднимая воркер.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.db import session_factory
from app.core.jobs import DISPATCH_PUSH
from app.jobs.tasks.context import attempt
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.modules.push.application.dispatcher import PushDispatcher
from app.modules.push.infrastructure.repositories.device_repository import DeviceRepository
from app.shared.push.factory import build_push_transport


async def dispatch_push(ctx: dict[str, Any], event_type: str, clinic_id: str) -> int:
    """Разослать пуш события всем адресатам клиники; вернуть число отправок."""
    async with JobRunRecorder(DISPATCH_PUSH, attempt(ctx)) as run:
        async with session_factory() as session:
            dispatcher = PushDispatcher(session, build_push_transport(), DeviceRepository(session))
            sent = await dispatcher.dispatch(event_type, uuid.UUID(clinic_id))
        run.note(f"{event_type} → клиника {clinic_id}: устройств {sent}")
        return sent
