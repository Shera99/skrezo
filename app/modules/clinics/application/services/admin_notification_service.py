"""Уведомление владельца созданной клиники: данные для входа сообщением.

Отправка фоновая (см. :class:`Notifier`) — провижининг уже произошёл, и сбой
канала его не отменяет.
"""

from __future__ import annotations

from app.modules.clinics.domain.messages import clinic_created_text
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.notifications.notifier import Notifier


class AdminNotificationService:
    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def notify_account_created(self, account: Account, clinic: Clinic) -> None:
        """Сообщить владельцу, что кабинет создан и вход — по коду на его номер.

        Зовётся только с админского пути (``POST /admin/clients``): владелец
        узнаёт о кабинете, которого не заводил. Соло-врач регистрируется сам и в
        конце регистрации уже внутри — ему это сообщение не отправляется.
        """
        await self._notifier.notify_phone(
            account.phone,
            clinic_created_text(clinic_name=clinic.name, phone=account.phone),
            event="clinic_created",
        )
