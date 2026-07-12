"""Arq-воркер фоновых задач.

Реальные джобы биллинга (скан конца триала/продления, уведомления при смене
тарифа) появятся на фазе 5. Пока зарегистрирована одна функция-заглушка —
arq не стартует без хотя бы одной функции или крон-задачи.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.core.config import settings


async def noop(ctx: dict[str, Any]) -> None:
    """Пустая задача-заглушка (в очередь не ставится). Удалить на фазе 5."""
    return None


class WorkerSettings:
    """Настройки arq-воркера."""

    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions: ClassVar[list[Any]] = [noop]  # заполнится джобами биллинга на фазе 5
    cron_jobs: ClassVar[list[Any]] = []  # скан подписок по расписанию (фаза 5)
