"""Напоминание о передаче, на которую никто не ответил.

Инцидент 22–31.10.2026: пациент четыре раза просил администратора, каждая
просьба становилась передачей с пушем ``ai.escalated`` — и ни одна не дошла до
человека: пуш пропустили, а второго не было. Пациент десять дней писал в
пустоту.

Теперь передача, которая ``AI_ESCALATION_REMINDER_MINUTES`` (30 минут) висит
без ответа сотрудника, напоминает о себе один раз — событием
``ai.escalation_reminder`` владельцу и менеджеру. Не напоминаем, если:

* сотрудник уже ответил пациенту или сам взял диалог («отвечаю сам»);
* диалог вернулся боту (автовозврат, ручной возврат) или закрыт;
* передаче больше суток — окно WhatsApp закрыто, писать пациенту всё равно
  можно только шаблоном.

**Тихие часы клиники** (вне графика, выходной) напоминание не тратят: обычный
пуш в это время не ставится вовсе, и отметка «напомнили» съела бы его — к
утру никто ничего не получил бы. Передача ждёт открытия клиники, а пациенту
при передаче уже сказано, когда ответят.

Отметка «напомнили» — ``payload.remindedAt`` у самой передачи: напоминание
одно на передачу, повторный запуск задачи его не дублирует.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.modules.push.application.outbox import emit_event
from app.modules.push.application.quiet_service import QuietService
from app.modules.whatsapp.domain.window import WINDOW
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation

logger = structlog.get_logger(__name__)


class EscalationReminderService:
    """Одно напоминание владельцу и менеджеру о передаче без ответа сотрудника."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = AiRepository(session)
        self._quiet = QuietService(session)

    async def run(self, now: datetime | None = None) -> list[uuid.UUID]:
        """Напомнить о всех передачах без ответа; вернуть их диалоги.

        ``AI_ESCALATION_REMINDER_MINUTES=0`` выключает напоминания.
        """
        minutes = settings.ai_escalation_reminder_minutes
        if minutes <= 0:
            return []
        moment = now or datetime.now(UTC)
        cutoff = moment - timedelta(minutes=minutes)
        since = moment - WINDOW
        candidates = await self._repository.escalations_to_remind(cutoff=cutoff, since=since)
        quiet: dict[uuid.UUID, bool] = {}
        reminded: list[uuid.UUID] = []
        for escalation_id, conversation_id, clinic_id in candidates:
            if clinic_id not in quiet:
                quiet[clinic_id] = (await self._quiet.moment(clinic_id, moment)).quiet
            if quiet[clinic_id]:
                continue  # клиника закрыта — напомним на первом прогоне после открытия
            # Блокировка диалога, как у возврата боту: тот же порядок блокировок,
            # и между выборкой и блокировкой сотрудник мог ответить или забрать
            # диалог — условия перепроверяются уже под ней.
            await self._session.get(AiConversation, conversation_id, with_for_update=True)
            still = await self._repository.escalations_to_remind(
                cutoff=cutoff, since=since, conversation_id=conversation_id
            )
            if not any(row[0] == escalation_id for row in still):
                continue
            escalation = await self._session.get(AiEscalation, escalation_id, with_for_update=True)
            if escalation is None:
                continue
            # Новый словарь, а не правка на месте: JSONB без MutableDict не
            # замечает изменений внутри.
            escalation.payload = {**(escalation.payload or {}), "remindedAt": moment.isoformat()}
            await emit_event(
                self._session,
                clinic_id=escalation.clinic_id,
                type_="ai.escalation_reminder",
                entity_type="conversation",
                entity_id=escalation.conversation_id,
                payload={"reason": escalation.reason},
            )
            reminded.append(escalation.conversation_id)
        await self._session.commit()
        if reminded:
            logger.info("ai_escalations_reminded", count=len(reminded), minutes=minutes)
        return reminded
