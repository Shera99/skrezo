"""Ф8: ответ ассистента отдельным заданием — лок, дебаунс, отложки, резюме.

Проверяется поведение под нагрузкой, найденное разбором 04.09:

* приём вебхука больше не отвечает сам — ставит задание на диалог;
* два быстрых сообщения дают ОДИН ответ, а повтор задания — ни одного;
* занятый диалог откладывается, а не отвечается вторым заданием;
* временно недоступная модель откладывает ход, а не эскалирует; человека
  зовём только после всех отложек;
* резюме закрытых диалогов ставятся по заданию, а не пачкой в cron.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fakeredis import aioredis
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.jobs import AI_REPLY, SUMMARIZE_CONVERSATION
from app.jobs.tasks import ai as ai_job
from app.jobs.tasks import whatsapp as wa_job
from app.modules.ai.application.reply_coordinator import ConversationLock, ReplyCoordinator
from app.modules.whatsapp.domain.window import WINDOW
from app.shared.ai import AiProviderUnavailableError, AiResult, ToolCallRequest
from app.shared.db.enums import AiConversationStatus
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_summary import AiSummary
from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient
from tests.api.conftest import Ctx, _FakeProvider, _patch_job, _reply_json, _set_mode
from tests.conftest import Resources


@dataclass
class _Queue:
    """Redis arq в тестах: только запись поставленных заданий."""

    jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)

    async def enqueue_job(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((name, args, kwargs))


def _payload(phone_number_id: str, from_phone: str, text: str) -> dict[str, Any]:
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
                            "messages": [
                                {
                                    "from": from_phone.lstrip("+"),
                                    "id": f"wamid.J{uuid.uuid4().hex}",
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }


async def _conversation_id(res: Resources, phone: str) -> uuid.UUID:
    async with res.factory() as session:
        conv = (
            await session.execute(select(AiConversation).where(AiConversation.wa_phone == phone))
        ).scalar_one()
        return conv.id


def _coordinator(
    res: Resources,
    provider: Any,
    *,
    redis: Any = None,
    enqueue: _Queue | None = None,
    max_defers: int = 5,
) -> ReplyCoordinator:
    async def _enqueue(conversation_id: uuid.UUID, defer_count: int) -> None:
        assert enqueue is not None
        await enqueue.enqueue_job(AI_REPLY, str(conversation_id), defer_count)

    return ReplyCoordinator(
        session_factory=res.factory,
        provider_factory=lambda: provider,
        wa_client_factory=LoggingWhatsAppClient,
        lock=ConversationLock(redis, ttl_seconds=60) if redis is not None else None,
        enqueue=_enqueue if enqueue is not None else None,
        max_defers=max_defers,
    )


def _patch_jobs(monkeypatch: pytest.MonkeyPatch, provider: _FakeProvider, res: Resources) -> None:
    _patch_job(monkeypatch, wa_job, provider, res)
    monkeypatch.setattr(ai_job, "session_factory", res.factory)
    monkeypatch.setattr(ai_job, "build_ai_provider", lambda *_a, **_k: provider)


# ── Приём ставит задание, а не отвечает ─────────────────────────────────────


async def test_inbound_job_skips_queue_when_assistant_off(
    ctx: Ctx, fake: _FakeProvider, _resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ai_mode=off (дефолт новой клиники): сообщение сохраняется, задания нет."""
    _patch_jobs(monkeypatch, fake, _resources)
    queue = _Queue()
    saved = await wa_job.process_wa_inbound(
        {"redis": queue}, _payload(ctx.phone_number_id, "+996700200099", "Есть кто?")
    )
    assert saved == 1
    assert queue.jobs == []
    assert fake.calls == []


async def test_inbound_job_enqueues_reply_with_debounce(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    _patch_jobs(monkeypatch, fake, _resources)
    queue = _Queue()
    phone = "+996700200100"

    saved = await wa_job.process_wa_inbound(
        {"redis": queue}, _payload(ctx.phone_number_id, phone, "Здравствуйте")
    )

    assert saved == 1
    assert fake.calls == []  # модель в приёме не зовётся
    conv_id = await _conversation_id(_resources, phone)
    assert queue.jobs == [
        (AI_REPLY, (str(conv_id), 0), {"_defer_by": settings.ai_reply_debounce_seconds})
    ]


async def test_burst_of_messages_gets_one_reply_and_repeat_job_none(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Два сообщения подряд → одно задание отвечает на оба, второе выходит молча."""
    await _set_mode(admin_client, ctx, "auto")
    _patch_jobs(monkeypatch, fake, _resources)
    queue = _Queue()
    phone = "+996700200101"
    await wa_job.process_wa_inbound(
        {"redis": queue}, _payload(ctx.phone_number_id, phone, "Здравствуйте")
    )
    await wa_job.process_wa_inbound(
        {"redis": queue}, _payload(ctx.phone_number_id, phone, "Сколько стоит чистка?")
    )
    assert len(queue.jobs) == 2
    conv_id = await _conversation_id(_resources, phone)

    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чистка — уточню."))]
    redis = aioredis.FakeRedis(decode_responses=True)
    coordinator = _coordinator(_resources, fake, redis=redis, enqueue=_Queue())

    first = await coordinator.run(conv_id)
    assert first.status == "sent"
    assert len(fake.calls) == 1
    user_parts = [p for p in fake.calls[0]["parts"] if p.role == "user"]
    assert [p.text for p in user_parts] == ["Здравствуйте", "Сколько стоит чистка?"]

    second = await coordinator.run(conv_id)  # задание от второго сообщения
    assert second.status == "skipped"
    assert second.outcome is not None and second.outcome.note == "already_answered"
    assert len(fake.calls) == 1  # модель второй раз не звали
    assert await redis.get(f"ai:reply:lock:{conv_id}") is None  # лок снят


async def test_busy_conversation_is_deferred_then_dropped(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    _patch_jobs(monkeypatch, fake, _resources)
    phone = "+996700200102"
    await wa_job.process_wa_inbound(
        {"redis": _Queue()}, _payload(ctx.phone_number_id, phone, "Привет")
    )
    conv_id = await _conversation_id(_resources, phone)

    redis = aioredis.FakeRedis(decode_responses=True)
    await redis.set(f"ai:reply:lock:{conv_id}", "чужой-токен", ex=60)  # диалог занят
    queue = _Queue()
    coordinator = _coordinator(_resources, fake, redis=redis, enqueue=queue, max_defers=2)

    assert (await coordinator.run(conv_id, defer_count=0)).status == "deferred"
    assert queue.jobs == [(AI_REPLY, (str(conv_id), 1), {})]
    assert fake.calls == []
    # Все отложки исчерпаны — отпускаем: у ответа есть владелец.
    assert (await coordinator.run(conv_id, defer_count=2)).status == "dropped"
    assert await redis.get(f"ai:reply:lock:{conv_id}") == "чужой-токен"  # чужой лок цел


# ── Недоступная модель ──────────────────────────────────────────────────────


@dataclass
class _DownProvider:
    model_name: str = "down"
    calls: int = 0

    async def complete(self, **_: Any) -> AiResult:
        self.calls += 1
        raise AiProviderUnavailableError("Gemini: HTTP 503")


async def test_provider_outage_defers_without_escalation_then_gives_up(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    _patch_jobs(monkeypatch, fake, _resources)
    phone = "+996700200103"
    await wa_job.process_wa_inbound(
        {"redis": _Queue()}, _payload(ctx.phone_number_id, phone, "Здравствуйте")
    )
    conv_id = await _conversation_id(_resources, phone)

    down = _DownProvider()
    queue = _Queue()
    coordinator = _coordinator(_resources, down, enqueue=queue, max_defers=3)

    result = await coordinator.run(conv_id, defer_count=0)
    assert result.status == "deferred"
    assert queue.jobs == [(AI_REPLY, (str(conv_id), 1), {})]
    async with _resources.factory() as session:
        assert (await session.execute(select(AiEscalation))).first() is None  # не лавина
        conv = await session.get(AiConversation, conv_id)
        assert conv is not None and conv.status is AiConversationStatus.ACTIVE

    # Последняя отложка исчерпана — честно передаём человеку.
    final = await coordinator.run(conv_id, defer_count=3)
    assert final.status == "given_up"
    async with _resources.factory() as session:
        escalation = (await session.execute(select(AiEscalation))).scalar_one()
        assert escalation.reason == "tool_error"
        conv = await session.get(AiConversation, conv_id)
        assert conv is not None and conv.status is AiConversationStatus.ESCALATED


async def test_bot_message_part_still_reaches_model_after_commit_release(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Соединение отпускается перед вызовом модели — ход при этом целостен."""
    await _set_mode(admin_client, ctx, "auto")
    _patch_jobs(monkeypatch, fake, _resources)
    phone = "+996700200104"
    await wa_job.process_wa_inbound(
        {"redis": _Queue()}, _payload(ctx.phone_number_id, phone, "Почём чистка?")
    )
    conv_id = await _conversation_id(_resources, phone)
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="get_services", args={"query": "чистка"})),
        AiResult(text=_reply_json("Чистка стоит 2 500 сом.", facts=["service:x"])),
    ]
    result = await _coordinator(_resources, fake).run(conv_id)
    assert result.status == "sent"
    assert len(fake.calls) == 2
    tool_parts = [p for p in fake.calls[1]["parts"] if p.role == "tool"]
    assert tool_parts and "2 500" in str(tool_parts[-1].tool_result)


# ── Резюме отдельными заданиями ─────────────────────────────────────────────


async def test_close_stale_enqueues_summary_jobs(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    _patch_jobs(monkeypatch, fake, _resources)
    phone = "+996700200105"
    await wa_job.process_wa_inbound(
        {"redis": _Queue()}, _payload(ctx.phone_number_id, phone, "Добрый день")
    )
    conv_id = await _conversation_id(_resources, phone)
    async with _resources.factory() as session:
        conv = await session.get(AiConversation, conv_id)
        assert conv is not None
        conv.last_msg_at = datetime.now(UTC) - WINDOW - timedelta(minutes=5)
        await session.commit()

    queue = _Queue()
    closed = await wa_job.close_stale_conversations({"redis": queue})
    assert closed == 1
    assert queue.jobs == [(SUMMARIZE_CONVERSATION, (str(conv_id),), {})]
    async with _resources.factory() as session:
        assert (await session.execute(select(AiSummary))).first() is None  # ещё нет

    fake.script = [AiResult(text="Пациент поздоровался, вопросов не было.")]
    assert await ai_job.summarize_conversation({}, str(conv_id)) is True
    async with _resources.factory() as session:
        summary = (await session.execute(select(AiSummary))).scalar_one()
        assert summary.conversation_id == conv_id
