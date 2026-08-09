"""FastAPI-зависимость: :class:`Notifier` поверх очереди задач."""

from __future__ import annotations

from fastapi import Depends

from app.core.jobs import JobQueue, get_job_queue
from app.shared.notifications.delivery import TextDeliveryService
from app.shared.notifications.factory import build_team_chat, build_text_channels
from app.shared.notifications.notifier import Notifier


def get_notifier(queue: JobQueue = Depends(get_job_queue)) -> Notifier:
    """Отправитель уведомлений: очередь воркера + каналы на случай, если её нет."""
    return Notifier(queue, TextDeliveryService(build_text_channels()), build_team_chat())
