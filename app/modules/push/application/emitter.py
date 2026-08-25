"""Эмиссия пуш-событий из обработчика запроса — через очередь, не роняя операцию.

Обработчик не зовёт диспетчер напрямую (тот резолвит получателей и ходит в
FCM/APNs — это не должно задерживать ответ и не должно уметь отменить уже
сделанное). Он ставит задачу ``dispatch_push`` в очередь arq, а рассылку делает
воркер. Зовётся **после commit** use-case'а: пуш о событии, которого в БД нет,
хуже отсутствия пуша.

Если очередь недоступна (Redis лёг, а в тестах её просто нет — lifespan там не
запускается), событие **пропускается**: ``sync.day`` — лишь подсказка приложению
перечитать день, и потерять её не страшно (в отличие от OTP, который в такой
ситуации доставляют на месте).
"""

from __future__ import annotations

import uuid

import structlog

from app.core.jobs import DISPATCH_PUSH, JobQueue

logger = structlog.get_logger(__name__)


class PushEmitter:
    """Ставит рассылку пуш-события в очередь воркера."""

    def __init__(self, queue: JobQueue) -> None:
        self._queue = queue

    async def emit_sync_day(self, clinic_id: uuid.UUID) -> None:
        """Тихий ``sync.day``: приложение перечитает сегодня/завтра.

        Без ``job_id`` (не схлопываем): каждое изменение — своя задача. Лишний
        тихий пуш дешевле пропущенного обновления, а iOS всё равно коалесцирует
        silent-пуши сам. Схлопывание по временно́му окну — возможная оптимизация
        позже, но у arq ``job_id`` живёт до ``keep_result`` (час), и фиксированный
        id молча съел бы почти все обновления.
        """
        if not await self._queue.enqueue(DISPATCH_PUSH, "sync.day", str(clinic_id)):
            # ⚠️ Метку кладём под ``push_event``, а не ``event``: у structlog первый
            # позиционный аргумент называется event, и одноимённый kwarg роняет сам
            # вызов логирования (те же грабли, что в notifier.py).
            logger.info("push_emit_skipped", push_event="sync.day", clinic_id=str(clinic_id))
