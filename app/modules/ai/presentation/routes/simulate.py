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
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFoundError
from app.modules.ai.application.reply_service import ProcessAiReplyService, ReplyOutcome
from app.modules.ai.presentation.deps import get_ai_provider, get_wa_client_for_ai
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
from app.shared.ai import AiProvider
from app.shared.db.models.ai_message import AiMessage
from app.shared.speech import build_speech_provider
from app.shared.whatsapp_cloud import WhatsAppCloudClient
from app.shared.whatsapp_cloud.dry_run_client import DRY_RUN_PREFIX, DryRunWhatsAppClient

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
    provider: AiProvider | None = Depends(get_ai_provider),
    wa_client: WhatsAppCloudClient = Depends(get_wa_client_for_ai),
) -> SimulateInboundResponse:
    """Прогнать текст через боевой конвейер и вернуть результат синхронно.

    Требуется привязка номера клинике (`PUT /admin/clients/{id}/whatsapp`) и
    включённый режим ассистента (`PUT /admin/clients/{id}/ai-mode`). При
    `shadow` ответ бота виден здесь, но пациенту бы не ушёл; при `off`
    сообщение просто сохраняется — как до Ф2.

    `dryRun` — пробный прогон для режима `auto`: конвейер работает как боевой,
    но ответ, кнопки и шаблоны в WhatsApp не уходят (у таких сообщений
    `sent: false`), а нажатие кнопки не создаёт приём и не меняет запись.
    Диалог сохраняется и виден в кабинете клиники. Резерв слота, карточка
    пациента и события для инбокса создаются как в боевом прогоне.
    """
    repository = WhatsAppRepository(session)
    binding = await repository.binding_by_clinic(body.clinic_id)
    if binding is None or not binding.active:
        raise NotFoundError("WhatsApp не подключён к клинике", code="whatsapp_not_connected")

    # Один клиент на приём и на ответ: в пробном прогоне оба не шлют наружу.
    client: WhatsAppCloudClient = DryRunWhatsAppClient(wa_client) if body.dry_run else wa_client
    processor = InboundProcessor(
        session, repository, client, build_speech_provider(), dry_run=body.dry_run
    )
    refs = await processor.process(
        _meta_payload(binding.phone_number_id, body.from_phone, body.text, body.button_payload)
    )
    if body.button_payload:
        # Кнопка обработана детерминированно (мимо модели) — вернуть диалог.
        phone = body.from_phone if body.from_phone.startswith("+") else f"+{body.from_phone}"
        conversation = await repository.conversation_by_phone(body.clinic_id, phone)
        if conversation is None:
            raise NotFoundError("Диалог не найден", code="conversation_not_found")
        conversation_id = conversation.id
        outcome = ReplyOutcome(status="button")
    else:
        if not refs:
            raise NotFoundError("Сообщение не сохранилось", code="message_not_saved")
        conversation_id = refs[0].conversation_id
        outcome = await ProcessAiReplyService(session, provider, client).execute(conversation_id)
    if body.dry_run:
        await _unmark_dry_run(session, conversation_id)

    tail = list(
        (
            await session.execute(
                select(AiMessage)
                .where(AiMessage.conversation_id == conversation_id)
                .order_by(AiMessage.id.desc())
                .limit(10)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    return SimulateInboundResponse(
        conversation_id=conversation_id,
        outcome=outcome.status,
        dry_run=body.dry_run,
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


async def _unmark_dry_run(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Снять «отправлено» с сообщений пробного прогона — в WhatsApp они не уходили.

    Конвейер отработал как боевой и записал фальшивые ``wamid`` подменённого
    клиента; в кабинете такие сообщения должны выглядеть неотправленными.
    """
    await session.execute(
        update(AiMessage)
        .where(
            AiMessage.conversation_id == conversation_id,
            AiMessage.wa_message_id.like(f"{DRY_RUN_PREFIX}%"),
        )
        .values(sent_at=None, wa_message_id=None)
    )
    await session.commit()
