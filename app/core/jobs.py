"""Очередь фоновых задач (arq): постановка задачи из обработчика запроса.

Воркер до этого умел только расписание — задачу «сделай вот это, но не сейчас»
поставить было неоткуда. Пул подключений живёт рядом с клиентом Redis: один на
процесс, открывается в lifespan приложения и закрывается вместе с ним.

Имена джоб лежат здесь же, а не в модуле воркера: их знают обе стороны, и общая
константа — единственный способ не разъехаться (arq ищет функцию по строке и на
опечатку отвечает молчанием — задача просто не выполнится).

⚠️ Без lifespan пула нет (так живут тесты: ASGI-транспорт его не запускает), и
:meth:`JobQueue.enqueue` честно возвращает ``False``. Вызывающий на это смотрит
и решает сам — обычно сделать работу на месте, а не потерять её.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import structlog
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from redis.exceptions import RedisError

from app.core.config import settings

logger = structlog.get_logger(__name__)

#: Доставить сообщение на телефон (цепочка каналов).
SEND_NOTIFICATION = "send_notification"
#: Доставить сообщение в чат команды Skrezo.
SEND_TEAM_NOTIFICATION = "send_team_notification"
#: Разослать пуш-событие на устройства клиники (FCM/APNs) — фан-аут в воркере.
DISPATCH_PUSH = "dispatch_push"

#: Отметка «воркер жив». Пишется чаще, чем истекает: пропущенный тик не должен
#: выглядеть как смерть воркера, а вот остановка — должна, и быстро.
WORKER_HEARTBEAT_KEY = "worker:heartbeat"
WORKER_HEARTBEAT_TTL_SECONDS = 180

_pool: ArqRedis | None = None


async def connect_job_queue() -> None:
    """Открыть пул очереди (вызывается из lifespan приложения).

    Недоступный Redis на старте не роняет приложение: очередь — не единственная
    его функция, а без неё задачи выполнятся на месте. Ошибка уходит в лог.
    """
    global _pool
    try:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    except (RedisError, OSError) as exc:
        logger.error("job_queue_connect_failed", error=str(exc))
        _pool = None


async def close_job_queue() -> None:
    """Закрыть пул очереди (вызывается из lifespan приложения)."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


class JobQueue:
    """Постановка задач в очередь воркера."""

    def __init__(self, pool: ArqRedis | None) -> None:
        self._pool = pool

    async def enqueue(
        self,
        name: str,
        *args: Any,
        job_id: str | None = None,
        defer_by: timedelta | None = None,
    ) -> bool:
        """Поставить задачу; ``False`` — очередь недоступна, работать надо самому.

        ``job_id`` даёт идемпотентность: задача с уже занятым идентификатором не
        ставится второй раз. Такой отказ — это **успех** (задача уже в очереди),
        поэтому здесь он и не отличается от постановки: иначе вызывающий увидел
        бы ``False`` и сделал работу на месте, то есть ровно продублировал бы её.
        """
        if self._pool is None:
            logger.warning("job_queue_unavailable", job=name)
            return False
        try:
            await self._pool.enqueue_job(name, *args, _job_id=job_id, _defer_by=defer_by)
        except (RedisError, OSError) as exc:
            logger.error("job_enqueue_failed", job=name, error=str(exc))
            return False
        return True


def get_job_queue() -> JobQueue:
    """FastAPI-зависимость: очередь задач (общий пул процесса)."""
    return JobQueue(_pool)
