"""Общее для всех задач: разбор контекста arq.

Контекст задачи — обычный словарь, и обращаться к нему по ключу из каждой джобы
значит однажды получить ``KeyError`` там, где джоба вызвана напрямую (тест,
ручной прогон). Поэтому доступ к нему в одном месте и с запасным значением.
"""

from __future__ import annotations

from typing import Any

from app.core.jobs import JobQueue
from app.shared.notifications.delivery import TextDeliveryService
from app.shared.notifications.factory import build_team_chat, build_text_channels
from app.shared.notifications.notifier import Notifier


def attempt(ctx: dict[str, Any]) -> int:
    """Номер попытки arq (1 — первая); вне воркера контекста нет."""
    return int(ctx.get("job_try") or 1)


def notifier(ctx: dict[str, Any]) -> Notifier:
    """Отправитель для задач воркера: ставит доставку отдельными задачами.

    Пул берётся из контекста arq — тот самый, которым воркер и живёт. Сообщение
    каждой клинике становится своей задачей: одна недоставка не отменяет
    рассылку остальным и повторяется отдельно от них.
    """
    return Notifier(
        JobQueue(ctx.get("redis")),
        TextDeliveryService(build_text_channels()),
        build_team_chat(),
    )
