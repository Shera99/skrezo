"""Задачи доставки сообщений — по событию, поставленные из обработчика запроса.

Эти задачи, в отличие от задач по расписанию, повторять нужно: провайдер мог
просто не ответить. Недоставка превращается в ``Retry`` с растущей задержкой;
после ``max_tries`` попыток arq оставит задачу проваленной — и это видно в
журнале запусков, а не только в логе.
"""

from __future__ import annotations

from typing import Any

from arq import Retry

from app.core.jobs import SEND_NOTIFICATION, SEND_TEAM_NOTIFICATION
from app.jobs.tasks.context import attempt
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.shared.notifications.channels import MessageDeliveryError
from app.shared.notifications.delivery import TextDeliveryService
from app.shared.notifications.factory import build_team_chat, build_text_channels


async def send_notification(ctx: dict[str, Any], phone: str, text: str, event: str) -> str:
    """Доставить сообщение на телефон цепочкой каналов."""
    async with JobRunRecorder(SEND_NOTIFICATION, attempt(ctx)) as run:
        delivery = TextDeliveryService(build_text_channels())
        try:
            await delivery.send(phone, text)
        except MessageDeliveryError as exc:
            run.note(f"{event} → {phone}: {exc}")
            raise Retry(defer=attempt(ctx) * 60) from exc
        run.note(f"{event} → {phone}")
        return "sent"


async def send_team_notification(ctx: dict[str, Any], text: str, event: str) -> str:
    """Доставить сообщение в чат команды Skrezo."""
    async with JobRunRecorder(SEND_TEAM_NOTIFICATION, attempt(ctx)) as run:
        chat = build_team_chat()
        if chat is None:
            run.note("чат команды не настроен")
            return "skipped"
        try:
            await chat.send(text)
        except MessageDeliveryError as exc:
            run.note(f"{event}: {exc}")
            raise Retry(defer=attempt(ctx) * 60) from exc
        run.note(event)
        return "sent"
