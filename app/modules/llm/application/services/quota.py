"""Лимиты голосового помощника: на сотрудника и на клинику (решение 12.10.2026).

Считается каждое обращение к модели, а не вопрос врача: цикл ``voice_agent``
из трёх обращений — это 3. Отличить продолжение цикла от нового вопроса сервер
мог бы, но такое продолжение клиент подделывает, и лимит обходился бы.

Три фиксированных окна в Redis: минута и час на сотрудника (защита от
зацикливания и от одного «разговорчивого» врача), сутки на клинику (потолок
расхода). Превышение — 429 ``llm_quota_exceeded`` со сроком до сброса окна.
"""

from __future__ import annotations

import uuid

from redis.asyncio import Redis

from app.core.config import settings
from app.shared.api.rate_limit import incr_window, too_many_requests

QUOTA_EXCEEDED = "llm_quota_exceeded"
_MESSAGE = "Лимит запросов к ассистенту исчерпан"


class LlmQuota:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def consume(self, account_id: uuid.UUID, clinic_id: uuid.UUID) -> None:
        """Учесть одно обращение; лимит исчерпан — 429 с ``retryAfter``.

        Сначала проверяются ВСЕ окна, и только потом прибавляется во все: иначе
        отказ по суточному лимиту клиники всё равно съедал бы минутный и часовой
        лимит сотрудника, и после сброса суток он ещё час получал бы 429 за
        обращения, которых не было. Лимит мягкий: два одновременных запроса
        могут пройти на последнем свободном месте — это приемлемо.
        """
        windows = (
            (f"rl:llm:account:minute:{account_id}", settings.llm_app_limit_per_minute, 60),
            (f"rl:llm:account:hour:{account_id}", settings.llm_app_limit_per_hour, 3600),
            (f"rl:llm:clinic:day:{clinic_id}", settings.llm_app_clinic_limit_per_day, 86_400),
        )
        current = await self._redis.mget([key for key, _, _ in windows])
        for (key, limit, window), used in zip(windows, current, strict=True):
            if used is not None and int(used) >= limit:
                ttl = await self._redis.ttl(key)
                raise too_many_requests(
                    ttl if ttl and ttl > 0 else window, code=QUOTA_EXCEEDED, message=_MESSAGE
                )
        for key, _, window in windows:
            await incr_window(self._redis, key, window)
