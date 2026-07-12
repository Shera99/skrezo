"""Уведомление команды Skrezo о новом лиде (Telegram-чат).

v1 — заглушка (бот-токен/чат не настроены): логируем факт. Реальная отправка в
общий чат — после настройки бота.
"""

from __future__ import annotations

import structlog

from app.shared.db.models.lead import Lead

logger = structlog.get_logger(__name__)


class TeamNotificationService:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id

    async def notify_new_lead(self, lead: Lead) -> None:
        # TODO(phase 3+): отправка в Telegram-чат команды через Bot API.
        logger.info(
            "lead_created",
            lead_id=str(lead.id),
            clinic_name=lead.clinic_name,
            phone=lead.phone,
        )
