"""Уведомление владельца созданной клиники: данные для входа сообщением.

Отправка фоновая (см. :class:`Notifier`) — провижининг уже произошёл, и сбой
канала его не отменяет.
"""

from __future__ import annotations

import structlog

from app.modules.clinics.domain.messages import clinic_created_text
from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic
from app.shared.notifications.notifier import Notifier

logger = structlog.get_logger(__name__)


class AdminNotificationService:
    def __init__(self, notifier: Notifier) -> None:
        self._notifier = notifier

    async def notify_account_created(
        self, account: Account, clinic: Clinic, temp_password: str | None
    ) -> None:
        """Отправить владельцу вход и временный пароль.

        Пароля нет (соло-врач регистрируется сам и входит по коду) — отправлять
        нечего: такому аккаунту сообщение «войдите паролем» только помешает.
        """
        if temp_password is None:
            logger.info(
                "clinic_created_passwordless",
                account_id=str(account.id),
                clinic_id=str(clinic.id),
            )
            return

        await self._notifier.notify_phone(
            account.phone,
            clinic_created_text(
                clinic_name=clinic.name, phone=account.phone, temp_password=temp_password
            ),
            event="clinic_created",
        )
