"""Роут: POST /admin/ai/simulate-inbound — имитация входящего WhatsApp (Skrezo).

Собирает точно такой же payload, как шлёт Meta, и прогоняет через НАСТОЯЩИЙ
конвейер: разбор входящего → событие → ассистент → валидатор → режим клиники.
Тестируется весь боевой путь без номера, подписи и самого Meta — то, что
нужно для ручной проверки и демо. Ответ бота (или причина его молчания)
возвращается синхронно.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFoundError
from app.modules.ai.application.reply_service import ProcessAiReplyService, ReplyOutcome
from app.modules.ai.presentation.deps import get_reply_service
from app.modules.ai.presentation.schemas import (
    SimulatedMessageOut,
    SimulateInboundRequest,
    SimulateInboundResponse,
)
from app.modules.platform_auth.presentation.deps import get_authenticated_user_id
from app.modules.whatsapp.application.services.inbound import InboundProcessor
from app.modules.whatsapp.infrastructure.repositories.whatsapp_repository import (
    WhatsAppRepository,
)
from app.shared.db.models.ai_message import AiMessage
from app.shared.speech import build_speech_provider
from app.shared.whatsapp_cloud import build_whatsapp_client

router = APIRouter()

_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {
        "description": "У клиники нет активной привязки WhatsApp",
        "content": {"application/json": {"example": {"error": "whatsapp_not_connected"}}},
    },
}


def _meta_payload(
    phone_number_id: str, from_phone: str, text: str, button_payload: str | None
) -> dict[str, Any]:
    """Payload в форме вебхука Meta: текст либо нажатие interactive-кнопки."""
    message: dict[str, Any] = {
        "from": from_phone.lstrip("+"),
        "id": f"wamid.SIM{uuid.uuid4().hex}",
        "timestamp": str(int(time.time())),
    }
    if button_payload:
        message.update(
            {
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": button_payload, "title": "Подтвердить"},
                },
            }
        )
    else:
        message.update({"type": "text", "text": {"body": text}})
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_number_id},
                            "messages": [message],
                        },
                    }
                ]
            }
        ],
    }


@router.post(
    "/admin/ai/simulate-inbound",
    response_model=SimulateInboundResponse,
    summary="Имитировать входящее сообщение пациента",
    response_description="Диалог после прогона: что сохранилось и что ответил бот",
    responses=_ERRORS,
)
async def simulate_inbound(
    body: SimulateInboundRequest,
    _admin: uuid.UUID = Depends(get_authenticated_user_id),
    session: AsyncSession = Depends(get_session),
    reply_service: ProcessAiReplyService = Depends(get_reply_service),
) -> SimulateInboundResponse:
    """Прогнать текст через боевой конвейер и вернуть результат синхронно.

    Требуется привязка номера клинике (`PUT /admin/clients/{id}/whatsapp`) и
    включённый режим ассистента (`PUT /admin/clients/{id}/ai-mode`). При
    `shadow` ответ бота виден здесь, но пациенту бы не ушёл; при `off`
    сообщение просто сохраняется — как до Ф2.
    """
    repository = WhatsAppRepository(session)
    binding = await repository.binding_by_clinic(body.clinic_id)
    if binding is None or not binding.active:
        raise NotFoundError("WhatsApp не подключён к клинике", code="whatsapp_not_connected")

    processor = InboundProcessor(
        session, repository, build_whatsapp_client(), build_speech_provider()
    )
    refs = await processor.process(
        _meta_payload(binding.phone_number_id, body.from_phone, body.text, body.button_payload)
    )
    if body.button_payload:
        # Кнопка обработана детерминированно (мимо модели) — вернуть диалог.
        phone = body.from_phone if body.from_phone.startswith("+") else f"+{body.from_phone}"
        conversation = await repository.open_conversation(body.clinic_id, phone)
        if conversation is None:
            raise NotFoundError("Диалог не найден", code="conversation_not_found")
        conversation_id = conversation.id
        outcome = ReplyOutcome(status="button")
    else:
        if not refs:
            raise NotFoundError("Сообщение не сохранилось", code="message_not_saved")
        conversation_id = refs[0].conversation_id
        outcome = await reply_service.execute(conversation_id)

    tail = list(
        (
            await session.execute(
                select(AiMessage)
                .where(AiMessage.conversation_id == conversation_id)
                .order_by(AiMessage.id.desc())
                .limit(10)
            )
        ).scalars()
    )
    return SimulateInboundResponse(
        conversation_id=conversation_id,
        outcome=outcome.status,
        bot_reply=outcome.reply,
        escalation_reason=outcome.escalation_reason,
        messages=[
            SimulatedMessageOut(
                role=m.role.value,
                body=m.body,
                sent=m.sent_at is not None,
                model=m.model,
            )
            for m in reversed(tail)
        ],
    )
