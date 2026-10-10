"""Уведомление команды Skrezo о новой заявке с лендинга — в общий чат Telegram.

Адресат внутренний, поэтому канал не цепочка на телефон, а Bot API. Отправка
фоновая: заявка уже записана, и молчащий бот не повод отвечать посетителю
ошибкой. Чат не настроен — событие уходит в лог (dev-стенд).

Сообщение уходит только о лидах с лендинга; о заявках через OTP чат, как и
раньше, не пишет.
"""

from __future__ import annotations

from app.modules.clinic_applications.domain.messages import new_lead_text
from app.shared.db.models.clinic_application import ClinicApplication
from app.shared.notifications.notifier import Notifier


class TeamNotificationService:
    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def notify_new_lead(self, application: ClinicApplication) -> None:
        await self._notifier.notify_team(new_lead_text(application), event="lead_created")
