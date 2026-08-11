"""Уведомление добавленного сотрудника: данные для входа сообщением.

Отправка идёт фоном (см. :class:`Notifier`): недоставка не отменяет добавление,
а ответ владельцу не ждёт провайдера. Секрета в сообщении нет — сотрудник входит
по коду, который запрашивает сам.
"""

from __future__ import annotations

from app.modules.staff.domain.messages import staff_added_text
from app.shared.notifications.notifier import Notifier


class StaffNotificationService:
    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def notify_staff_added(self, *, phone: str, clinic_name: str) -> None:
        await self._notifier.notify_phone(
            phone,
            staff_added_text(clinic_name=clinic_name, phone=phone),
            event="staff_added",
        )
