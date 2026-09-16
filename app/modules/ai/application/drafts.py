"""Черновики режима «Проверяю» и передача диалога между ботом и человеком (Ф5).

Черновик — ответ бота с ``review = pending`` и без ``sent_at``: пациент его не
видел. Решение принимает сотрудник: отправить как есть (``approved``),
отправить с правкой (``edited`` — исходный текст остаётся в ``original_body``
как материал для обучения, Ф6) или отклонить (``discarded``). Черновик, который
не успели рассмотреть до следующего сообщения пациента, бот сам помечает
``superseded`` — и здесь такой отправить нельзя: ответ на устаревшую реплику
хуже молчания.

Передача диалога: «дальше отвечаю сам» ставит диалог в ``escalated`` с причиной
``manual_takeover`` — бот замолкает штатно, тем же правилом §8.4. «Вернуть боту»
закрывает открытые эскалации и, если пациент ждёт ответа, ставит задание
ассистенту — чтобы возврат не означал тишину.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, ConflictError, NotFoundError, ValidationAppError
from app.core.jobs import AI_REPLY, JobQueue
from app.modules.ai.application.regression_cases import RegressionCaseService
from app.modules.ai.infrastructure.repositories.ai_repository import AiRepository
from app.modules.whatsapp.domain.lifecycle import reopen_if_closed
from app.modules.whatsapp.domain.window import is_window_open
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.enums import AiConversationStatus, AiMessageRole
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_message import AiMessage
from app.shared.realtime import publish_realtime
from app.shared.whatsapp_cloud import WhatsAppCloudClient, WhatsAppSendError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ToggleResult:
    status: AiConversationStatus
    resolved_escalations: int


class DraftService:
    def __init__(
        self,
        session: AsyncSession,
        ai_repository: AiRepository,
        wa_repository: WhatsAppRepository,
        wa_client: WhatsAppCloudClient,
        queue: JobQueue | None = None,
    ) -> None:
        self._session = session
        self._ai = ai_repository
        self._wa = wa_repository
        self._client = wa_client
        self._queue = queue

    # ── Черновики ───────────────────────────────────────────────────────────

    async def send(
        self,
        clinic_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message_id: int,
        *,
        actor_id: uuid.UUID,
        body: str | None,
    ) -> AiMessage:
        """Отправить черновик пациенту — как есть или с правкой."""
        conversation, draft = await self._pending(clinic_id, conversation_id, message_id)
        if await self._ai.patient_wrote_after(conversation_id, draft.id):
            # Бот уже готовит (или подготовил) свежий черновик — старый устарел.
            raise ConflictError(
                "Пациент написал снова — этот черновик устарел, дождитесь нового",
                code="draft_stale",
            )
        binding = await self._wa.binding_by_clinic(clinic_id)
        if binding is None or not binding.active:
            raise ConflictError("WhatsApp не подключён к клинике", code="whatsapp_not_connected")
        now = datetime.now(UTC)
        if not is_window_open(conversation.last_patient_msg_at, now):
            raise ValidationAppError(
                "Окно 24 часов закрыто: доступны только одобренные шаблоны",
                code="window_closed",
            )
        text = (body or "").strip() or draft.body
        try:
            sent = await self._client.send_text(
                binding.phone_number_id, conversation.wa_phone, text
            )
        except WhatsAppSendError as exc:
            raise AppError(
                "Не удалось отправить сообщение в WhatsApp", code="wa_send_failed", status_code=502
            ) from exc

        if text != draft.body:
            # Правка — самый честный обучающий сигнал: что предлагал бот и что
            # на самом деле ушло. Исходник остаётся рядом.
            draft.original_body = draft.body
            draft.body = text
            draft.review = "edited"
        else:
            draft.review = "approved"
        draft.reviewed_by = actor_id
        draft.reviewed_at = now
        draft.sent_at = now
        draft.wa_message_id = sent.wa_message_id
        question = await self._question_before(conversation_id, draft.id)
        if question:
            # Ф6: одобренный (тем более правленый) ответ — эталон регрессии.
            await RegressionCaseService(self._session).from_reply(
                conversation,
                question=question,
                reply=text,
                edited=draft.review == "edited",
                message_id=draft.id,
                actor_id=actor_id,
            )
        conversation.last_msg_at = now
        await publish_realtime(
            self._session,
            clinic_id,
            "conversation.message",
            {"conversationId": str(conversation_id), "direction": "out"},
        )
        await self._session.commit()
        return draft

    async def discard(
        self,
        clinic_id: uuid.UUID,
        conversation_id: uuid.UUID,
        message_id: int,
        *,
        actor_id: uuid.UUID,
    ) -> AiMessage:
        """Отклонить черновик: пациенту ничего не уходит, отказ — сигнал для обучения."""
        _, draft = await self._pending(clinic_id, conversation_id, message_id)
        draft.review = "discarded"
        draft.reviewed_by = actor_id
        draft.reviewed_at = datetime.now(UTC)
        await publish_realtime(
            self._session,
            clinic_id,
            "conversation.message",
            {"conversationId": str(conversation_id), "direction": "draft"},
        )
        await self._session.commit()
        return draft

    async def _pending(
        self, clinic_id: uuid.UUID, conversation_id: uuid.UUID, message_id: int
    ) -> tuple[AiConversation, AiMessage]:
        conversation = await self._wa.conversation(clinic_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Диалог не найден", code="conversation_not_found")
        draft = await self._ai.pending_draft(clinic_id, conversation_id, message_id)
        if draft is None:
            raise ConflictError(
                "Черновик уже рассмотрен, устарел или не существует", code="draft_not_pending"
            )
        return conversation, draft

    # ── Кто ведёт диалог ────────────────────────────────────────────────────

    async def _question_before(self, conversation_id: uuid.UUID, message_id: int) -> str:
        """Вопрос пациента, на который отвечал черновик (последний перед ним)."""
        last = (
            await self._session.execute(
                select(AiMessage)
                .where(
                    AiMessage.conversation_id == conversation_id,
                    AiMessage.role == AiMessageRole.PATIENT,
                    AiMessage.id < message_id,
                )
                .order_by(AiMessage.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return ((last.transcript or last.body) if last else "").strip()

    async def toggle(
        self,
        clinic_id: uuid.UUID,
        conversation_id: uuid.UUID,
        *,
        active: bool,
        actor_id: uuid.UUID,
    ) -> ToggleResult:
        """``active=False`` — «дальше отвечаю сам»; ``True`` — вернуть диалог боту.

        Закрытый диалог (сутки тишины) — не препятствие: он постоянный и
        возобновляется переключателем так же, как новым сообщением.
        """
        conversation = await self._wa.conversation(clinic_id, conversation_id)
        if conversation is None:
            raise NotFoundError("Диалог не найден", code="conversation_not_found")
        reopen_if_closed(conversation)
        now = datetime.now(UTC)
        resolved = 0
        if active:
            resolved = await self._ai.resolve_escalations(
                conversation_id, actor_id=actor_id, now=now
            )
            conversation.status = AiConversationStatus.ACTIVE
        else:
            if conversation.status is not AiConversationStatus.ESCALATED:
                self._ai.add_escalation(
                    conversation_id,
                    clinic_id,
                    reason="manual_takeover",
                    payload={"by": str(actor_id)},
                )
            conversation.status = AiConversationStatus.ESCALATED
        await publish_realtime(
            self._session,
            clinic_id,
            "conversation.status",
            {"conversationId": str(conversation_id), "status": conversation.status.value},
        )
        await self._session.commit()

        if active and self._queue is not None and await self._patient_is_waiting(conversation_id):
            # Возврат боту не должен означать тишину: пациент уже ждёт.
            await self._queue.enqueue(AI_REPLY, str(conversation_id), 0)
        return ToggleResult(status=conversation.status, resolved_escalations=resolved)

    async def _patient_is_waiting(self, conversation_id: uuid.UUID) -> bool:
        last = await self._wa.last_message_role(conversation_id)
        return last is AiMessageRole.PATIENT
