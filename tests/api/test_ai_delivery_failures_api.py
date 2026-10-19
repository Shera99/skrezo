"""Интеграционные тесты: WhatsApp не доставил сообщение (переписка 16.10.2026).

Meta четырнадцать раз отклонила ответы одному пациенту (номер не в списке
разрешённых — тестовый режим), а в чате клиники остались только его
сообщения без ответа:

* причина отказа сохраняется у сообщения и видна в чате (``deliveryError``),
  в том числе когда Meta сообщает о ней позже статусом ``failed``;
* недоставленный ответ бота не теряется — он в переписке с ``sentAt = null``;
* постоянный отказ — «липкая» передача ``delivery_failed``: бот молчит и не
  зовёт модель на каждое новое сообщение, автовозврат его не трогает, а
  возврат сотрудником — новая попытка; временный — прежняя ``tool_error``.
"""

from __future__ import annotations

import itertools
import time
import uuid
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.jobs.tasks import ai as ai_tasks
from app.shared.ai import AiResult
from app.shared.db.enums import AiMessageRole
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_message import AiMessage
from app.shared.whatsapp_cloud.client import SentMessage, WhatsAppSendError
from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient
from tests.api.conftest import (
    Ctx,
    _FakeProvider,
    _patch_job,
    _reply_json,
    _set_mode,
    _simulate,
    _webhook,
)
from tests.conftest import API, Resources

_seq = itertools.count(1)
_NOT_ALLOWED = "номер пациента не в списке разрешённых получателей"


def _patient() -> str:
    return f"+99670061{next(_seq):04d}"


def _refuse(monkeypatch: pytest.MonkeyPatch, *, status: int | None, code: int | None) -> None:
    """Каждая отправка текста — отказ Graph API с этим статусом и кодом Meta."""

    async def refuse(self: Any, phone_number_id: str, to_phone: str, body: str) -> SentMessage:
        raise WhatsAppSendError(
            f"Graph API {status} ({code})",
            status=status,
            code=code,
            detail="Recipient phone number not in allowed list",
        )

    monkeypatch.setattr(LoggingWhatsAppClient, "send_text", refuse)


async def _messages(res: Resources, conversation_id: uuid.UUID) -> list[AiMessage]:
    async with res.factory() as session:
        return list(
            (
                await session.execute(
                    select(AiMessage)
                    .where(AiMessage.conversation_id == conversation_id)
                    .order_by(AiMessage.id)
                )
            ).scalars()
        )


async def _escalations(res: Resources, conversation_id: uuid.UUID) -> list[AiEscalation]:
    async with res.factory() as session:
        return list(
            (
                await session.execute(
                    select(AiEscalation)
                    .where(AiEscalation.conversation_id == conversation_id)
                    .order_by(AiEscalation.created_at)
                )
            ).scalars()
        )


async def test_permanent_refusal_keeps_reply_and_reason_and_stops_retrying(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Инцидент 16.10: номер не в списке разрешённых — ответ и причина в чате, без повторов."""
    _patch_job(monkeypatch, ai_tasks, fake, _resources)
    await _set_mode(admin_client, ctx, "auto")
    _refuse(monkeypatch, status=400, code=131030)
    phone = _patient()
    fake.script = [AiResult(text=_reply_json("Здравствуйте! На какой день вас записать?"))]

    body = await _simulate(admin_client, ctx, "Здравствуйте, хотел записаться на приём", phone)

    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "delivery_failed"
    conversation_id = uuid.UUID(body["conversationId"])
    patient, reply = await _messages(_resources, conversation_id)  # шаблона пациенту нет
    assert patient.role is AiMessageRole.PATIENT
    assert reply.role is AiMessageRole.BOT and reply.sent_at is None
    assert reply.body == "Здравствуйте! На какой день вас записать?"
    assert reply.delivery_error is not None and _NOT_ALLOWED in reply.delivery_error
    [escalation] = await _escalations(_resources, conversation_id)
    assert escalation.payload is not None
    assert escalation.payload["waCode"] == 131030
    assert escalation.payload["proposedMessageId"] == reply.id
    assert _NOT_ALLOWED in escalation.payload["note"]

    # Чат клиники показывает и ответ бота, и почему он не дошёл.
    history = await client.get(
        f"{API}/clinic/conversations/{conversation_id}/messages", headers=ctx.owner_headers
    )
    assert history.status_code == 200, history.text
    shown = next(m for m in history.json()["items"] if m["role"] == "bot")
    assert _NOT_ALLOWED in shown["deliveryError"] and shown["sentAt"] is None

    # Пациент пишет снова — модель не зовётся, новой передачи нет.
    calls = len(fake.calls)
    again = await _simulate(admin_client, ctx, "?", phone)
    assert again["outcome"] == "skipped"
    assert len(fake.calls) == calls
    # И автовозврат «липкую» передачу не снимает, даже через 20 минут.
    async with _resources.factory() as session:
        shift = timedelta(minutes=20)
        await session.execute(
            update(AiEscalation)
            .where(AiEscalation.conversation_id == conversation_id)
            .values(created_at=AiEscalation.created_at - shift)
        )
        await session.execute(
            update(AiMessage)
            .where(AiMessage.conversation_id == conversation_id)
            .values(created_at=AiMessage.created_at - shift)
        )
        await session.commit()
    assert await ai_tasks.return_escalations_to_bot({}) == 0
    assert len(await _escalations(_resources, conversation_id)) == 1


async def test_staff_return_to_bot_retries_delivery(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сотрудник вернул диалог боту — значит, причину исправили: бот пробует снова."""
    await _set_mode(admin_client, ctx, "auto")
    _refuse(monkeypatch, status=400, code=131030)
    phone = _patient()
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"))]
    body = await _simulate(admin_client, ctx, "Здравствуйте", phone)
    monkeypatch.undo()  # номер добавили в список разрешённых
    await _set_mode(admin_client, ctx, "auto")

    returned = await client.post(
        f"{API}/clinic/conversations/{body['conversationId']}/assistant",
        headers=ctx.owner_headers,
        json={"active": True},
    )
    assert returned.status_code == 200, returned.text
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?"))]
    retried = await _simulate(admin_client, ctx, "Здравствуйте", phone)

    assert retried["outcome"] == "sent"


async def test_temporary_refusal_stays_tool_error(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сеть или лимит Meta — передача прежняя (tool_error), автовозврат попробует снова."""
    await _set_mode(admin_client, ctx, "auto")
    _refuse(monkeypatch, status=None, code=None)
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"))]

    body = await _simulate(admin_client, ctx, "Здравствуйте", _patient())

    assert body["escalationReason"] == "tool_error"
    messages = await _messages(_resources, uuid.UUID(body["conversationId"]))
    # Шаблон передачи пытались отправить — и он тоже сохранён с причиной.
    notice = next(m for m in messages if m.role is AiMessageRole.SYSTEM)
    assert notice.sent_at is None
    assert notice.delivery_error is not None and "нет связи с WhatsApp" in notice.delivery_error


async def test_refused_complaint_template_goes_to_human(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Шаблон жалобы не ушёл — раньше это молча считалось «теневым режимом»."""
    await _set_mode(admin_client, ctx, "auto")
    _refuse(monkeypatch, status=400, code=131026)

    body = await _simulate(admin_client, ctx, "У меня 18 суб болит хотел лечить", _patient())

    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "delivery_failed"
    [template] = [
        m
        for m in await _messages(_resources, uuid.UUID(body["conversationId"]))
        if m.role is AiMessageRole.BOT
    ]
    assert template.delivery_error is not None and "нет WhatsApp" in template.delivery_error


async def test_failed_status_webhook_explains_undelivered_message(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Meta приняла отправку, а позже прислала статус failed с кодом — причина в чате."""
    await _set_mode(admin_client, ctx, "auto")
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?"))]
    body = await _simulate(admin_client, ctx, "Здравствуйте", _patient())
    conversation_id = uuid.UUID(body["conversationId"])
    reply = next(
        m for m in await _messages(_resources, conversation_id) if m.role is AiMessageRole.BOT
    )
    assert reply.wa_message_id

    await _webhook(
        client,
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": ctx.phone_number_id},
                                "statuses": [
                                    {
                                        "id": reply.wa_message_id,
                                        "status": "failed",
                                        "timestamp": str(int(time.time())),
                                        "recipient_id": "996700610000",
                                        "errors": [
                                            {
                                                "code": 131026,
                                                "title": "Message undeliverable",
                                                "error_data": {"details": "Message Undeliverable."},
                                            }
                                        ],
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        },
    )

    async with _resources.factory() as session:
        saved = await session.get(AiMessage, reply.id)
        assert saved is not None
        conversation = await session.get(AiConversation, conversation_id)
        assert conversation is not None
    assert saved.wa_status == "failed"
    assert saved.delivery_error is not None and "нет WhatsApp" in saved.delivery_error
