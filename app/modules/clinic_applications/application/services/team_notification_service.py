"""Уведомление команды Skrezo о новой заявке клиники — карточкой в канал Telegram.

Адресат внутренний, поэтому канал не цепочка на телефон, а Bot API. Отправка
фоновая: заявка уже записана, и молчащий бот не повод отвечать заявителю
ошибкой. Чат не настроен — событие уходит в лог (dev-стенд).

Пишем про обе «двери» очереди: и про лид с сайта, и про заявку из приложения
через OTP — команде важна каждая (раньше вторая проходила молча).
"""

from __future__ import annotations

from app.modules.clinic_applications.domain.messages import (
    new_application_buttons,
    new_application_text,
)
from app.modules.clinic_applications.domain.models import ApplicationRow
from app.shared.notifications.notifier import Notifier


class TeamNotificationService:
    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def notify_new_application(self, application: ApplicationRow) -> None:
        """Карточка заявки с кнопками «написать» в чат команды."""
        await self._notifier.notify_team(
            new_application_text(application),
            event="lead_created" if not application.phone_verified else "application_created",
            html=True,
            buttons=new_application_buttons(application),
        )
