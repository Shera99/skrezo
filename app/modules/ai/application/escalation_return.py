"""Автовозврат диалога боту, если администратор не взялся за него (§8.4).

Решение владельца 12.10.2026: передача человеку — пауза бота, но не вечная.
Если за ``AI_ESCALATION_RETURN_MINUTES`` (15 минут) никто из сотрудников не
ответил пациенту и не забрал диалог себе, диалог возвращается ассистенту.
Раньше пациент, которому бот сказал «уточню у администратора», мог писать в
пустоту часами: бот молчал до ручного возврата, а возвращать было некому.

Возвращается только диалог, где пациент **написал после передачи** (и не
раньше суток назад — окно WhatsApp): иначе бот снова взялся бы за то, что сам
же передал человеку (фото, сбой отправки), и эскалировал бы его по кругу.

Что НЕ возвращается (:data:`STICKY_ESCALATIONS`): «отвечаю сам» сотрудника,
неотложное состояние, сильная боль без свободного времени и поток сообщений —
там бот не помощник. Сотрудник уже ответил пациенту — тоже нет: разговор
ведёт человек.

Сам возврат не пишет пациенту ничего: на ожидающее сообщение ответит обычное
задание ассистента — его ставит cron-задача.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.modules.whatsapp.domain.window import WINDOW
from app.shared.db.enums import AiConversationStatus
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.realtime import publish_realtime

logger = structlog.get_logger(__name__)


class EscalationReturnService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = AiRepository(session)

    async def run(self, now: datetime | None = None) -> list[uuid.UUID]:
        """Вернуть боту все «зависшие» диалоги; вернуть их идентификаторы.

        ``AI_ESCALATION_RETURN_MINUTES=0`` выключает автовозврат.
        """
        minutes = settings.ai_escalation_return_minutes
        if minutes <= 0:
            return []
        moment = now or datetime.now(UTC)
        cutoff = moment - timedelta(minutes=minutes)
        waiting_since = moment - WINDOW
        candidates = await self._repository.escalations_to_return(
            cutoff=cutoff, waiting_since=waiting_since
        )
        returned: list[uuid.UUID] = []
        for conversation_id in candidates:
            conversation = await self._session.get(
                AiConversation, conversation_id, with_for_update=True
            )
            if conversation is None or conversation.status is not AiConversationStatus.ESCALATED:
                continue
            # Между выборкой и блокировкой сотрудник мог ответить пациенту или
            # нажать «отвечаю сам» — условия перепроверяются уже под блокировкой.
            if not await self._repository.escalations_to_return(
                cutoff=cutoff, waiting_since=waiting_since, conversation_id=conversation_id
            ):
                continue
            await self._repository.resolve_escalations(conversation_id, actor_id=None, now=moment)
            conversation.status = AiConversationStatus.ACTIVE
            # Тот же сигнал, что у ручного переключателя: чат перерисует статус.
            await publish_realtime(
                self._session,
                conversation.clinic_id,
                "conversation.status",
                {
                    "conversationId": str(conversation_id),
                    "status": conversation.status.value,
                    "autoReturned": True,
                },
            )
            returned.append(conversation_id)
        await self._session.commit()
        if returned:
            logger.info("ai_escalations_returned", count=len(returned), minutes=minutes)
        return returned
