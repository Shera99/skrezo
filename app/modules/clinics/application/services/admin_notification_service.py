"""Уведомление администратора созданной клиники (WhatsApp с инструкцией входа).

v1 — заглушка (провайдер не выбран): логируем факт.
"""

from __future__ import annotations

import structlog

from app.shared.db.models.account import Account
from app.shared.db.models.clinic import Clinic

logger = structlog.get_logger(__name__)


class AdminNotificationService:
    async def notify_account_created(self, account: Account, clinic: Clinic) -> None:
        # TODO(phase 3+): отправка WhatsApp с инструкцией входа на account.phone.
        logger.info(
            "account_created",
            account_id=str(account.id),
            clinic_id=str(clinic.id),
            phone=account.phone,
        )
