"""Уведомление команды Skrezo о новом лиде — в общий чат Telegram.

Адресат внутренний, поэтому канал не цепочка на телефон, а Bot API. Отправка
фоновая: лид уже записан, и молчащий бот не повод отвечать посетителю ошибкой.
Чат не настроен — событие уходит в лог (dev-стенд).
"""

from __future__ import annotations

from app.modules.leads.domain.messages import new_lead_text
from app.shared.db.models.lead import Lead
from app.shared.notifications.notifier import Notifier


class TeamNotificationService:
    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def notify_new_lead(self, lead: Lead) -> None:
        self._notifier.notify_team(new_lead_text(lead), event="lead_created")
