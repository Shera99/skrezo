"""FastAPI-зависимость: :class:`Notifier` с фоновой очередью текущего запроса."""

from __future__ import annotations

from fastapi import BackgroundTasks

from app.shared.notifications.delivery import TextDeliveryService
from app.shared.notifications.factory import build_team_chat, build_text_channels
from app.shared.notifications.notifier import Notifier


def get_notifier(background: BackgroundTasks) -> Notifier:
    """Отправитель уведомлений: каналы общие на процесс, очередь — на запрос."""
    return Notifier(background, TextDeliveryService(build_text_channels()), build_team_chat())
