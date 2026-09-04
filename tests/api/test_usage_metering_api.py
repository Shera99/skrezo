"""Учёт использования по клиникам: токены модели, диалоги, статусы WhatsApp.

Основа для тарифных пакетов и сметы себестоимости: каждый вызов модели
считается в токенах, диалог с ассистентом — один раз за всю жизнь, а платность
исходящих WhatsApp берётся из статусов доставки Меты (сумму она не присылает —
только категорию, в сомы переводит прайс-лист из настроек).
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.jobs.tasks import ai as ai_job
from app.shared.ai import AiResult, ToolCallRequest
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.clinic_usage_day import ClinicUsageDay
from tests.api.conftest import (
    Ctx,
    _FakeProvider,
    _inbound,
    _patch_job,
    _reply_json,
    _set_mode,
    _simulate,
    _webhook,
)
from tests.conftest import API, Resources


def _status_payload(
    phone_number_id: str,
    wa_message_id: str,
    status: str,
    *,
    billable: bool | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Payload вебхука Meta со статусом доставки нашего исходящего."""
    item: dict[str, Any] = {
        "id": wa_message_id,
        "status": status,
        "timestamp": str(int(time.time())),
        "recipient_id": "996700200400",
    }
    if billable is not None:
        item["pricing"] = {
            "billable": billable,
            "pricing_model": "PMP",
            "category": category,
            "type": "regular" if billable else "free_customer_service",
        }
        item["conversation"] = {"id": "conv-1", "origin": {"type": category}}
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
                            "statuses": [item],
                        },
                    }
                ]
            }
        ],
    }


async def _usage(res: Resources, clinic_id: str) -> ClinicUsageDay:
    async with res.factory() as session:
        rows = list(
            (
                await session.execute(
                    select(ClinicUsageDay).where(ClinicUsageDay.clinic_id == uuid.UUID(clinic_id))
                )
            ).scalars()
        )
    assert len(rows) == 1, [r.day for r in rows]
    return rows[0]


# ── Модель и диалоги ────────────────────────────────────────────────────────


async def test_model_calls_tokens_and_dialogs_are_metered(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    phone = "+996700200400"
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(name="get_services", args={"query": "чистка"}),
            usage={"input": 1200, "output": 30},
        ),
        AiResult(
            text=_reply_json("Чистка стоит 2 500 сом.", facts=["service:x"]),
            usage={"input": 1500, "output": 80},
        ),
    ]
    assert (await _simulate(admin_client, ctx, "Почём чистка?", phone))["outcome"] == "sent"

    row = await _usage(_resources, ctx.clinic_id)
    assert (row.model_calls, row.model_input_tokens, row.model_output_tokens) == (2, 2700, 110)
    assert (row.assistant_replies, row.dialogs) == (1, 1)
    assert row.day == datetime.now(UTC).date()

    # Второе сообщение того же пациента: вызов и ответ прибавились, диалог — нет.
    fake.script = [AiResult(text=_reply_json("Да, приходите."), usage={"input": 900, "output": 20})]
    await _simulate(admin_client, ctx, "А завтра можно?", phone)
    row = await _usage(_resources, ctx.clinic_id)
    assert (row.model_calls, row.model_input_tokens) == (3, 3600)
    assert (row.assistant_replies, row.dialogs) == (2, 1)


async def test_summary_job_meters_tokens(
    admin_client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _set_mode(admin_client, ctx, "shadow")
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"), usage={"input": 500, "output": 10})]
    body = await _simulate(admin_client, ctx, "Салам", "+996700200401")
    before = await _usage(_resources, ctx.clinic_id)

    _patch_job(monkeypatch, ai_job, fake, _resources)
    fake.script = [AiResult(text="Пациент поздоровался.", usage={"input": 400, "output": 40})]
    assert await ai_job.summarize_conversation({}, body["conversationId"]) is True

    after = await _usage(_resources, ctx.clinic_id)
    assert after.model_calls == before.model_calls + 1
    assert after.model_input_tokens == before.model_input_tokens + 400
    assert after.assistant_replies == before.assistant_replies  # резюме — не ответ пациенту


# ── Статусы WhatsApp ────────────────────────────────────────────────────────


async def _outgoing(client: AsyncClient, ctx: Ctx, res: Resources, phone: str, text: str) -> str:
    """Ответ из кабинета; вернуть wamid, который выдал (лог-)клиент."""
    await _webhook(client, _inbound(ctx.phone_number_id, phone, "Здравствуйте"))
    conv = (await client.get(f"{API}/clinic/conversations", headers=ctx.owner_headers)).json()
    conv_id = next(c["id"] for c in conv["list"] if c["waPhone"] == phone)
    sent = await client.post(
        f"{API}/clinic/conversations/{conv_id}/messages",
        headers=ctx.owner_headers,
        json={"body": text},
    )
    assert sent.status_code == 201, sent.text
    async with res.factory() as session:
        message = await session.get(AiMessage, sent.json()["id"])
        assert message is not None and message.wa_message_id
        return message.wa_message_id


async def _message(res: Resources, wa_message_id: str) -> AiMessage:
    async with res.factory() as session:
        return (
            await session.execute(select(AiMessage).where(AiMessage.wa_message_id == wa_message_id))
        ).scalar_one()


async def test_statuses_update_delivery_and_meter_billing_once(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    wamid = await _outgoing(client, ctx, _resources, "+996700200402", "Напоминаем о визите")

    await _webhook(
        client,
        _status_payload(ctx.phone_number_id, wamid, "sent", billable=True, category="utility"),
    )
    message = await _message(_resources, wamid)
    assert (message.wa_status, message.wa_pricing_category, message.wa_billable) == (
        "sent",
        "utility",
        True,
    )
    row = await _usage(_resources, ctx.clinic_id)
    assert (row.wa_sent, row.wa_utility) == (1, 1)

    # delivered с тем же pricing — статус вперёд, платность не удваивается.
    await _webhook(
        client,
        _status_payload(ctx.phone_number_id, wamid, "delivered", billable=True, category="utility"),
    )
    assert (await _message(_resources, wamid)).wa_status == "delivered"
    row = await _usage(_resources, ctx.clinic_id)
    assert (row.wa_sent, row.wa_utility) == (1, 1)

    # Запоздавший sent не откатывает delivered; read — двигает дальше.
    await _webhook(client, _status_payload(ctx.phone_number_id, wamid, "sent"))
    assert (await _message(_resources, wamid)).wa_status == "delivered"
    await _webhook(client, _status_payload(ctx.phone_number_id, wamid, "read"))
    assert (await _message(_resources, wamid)).wa_status == "read"


async def test_free_service_message_and_unknown_status_are_harmless(
    client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    wamid = await _outgoing(client, ctx, _resources, "+996700200403", "Конечно, ждём вас")
    await _webhook(
        client,
        _status_payload(ctx.phone_number_id, wamid, "sent", billable=False, category="service"),
    )
    row = await _usage(_resources, ctx.clinic_id)
    assert (row.wa_sent, row.wa_service_free, row.wa_utility) == (1, 1, 0)

    # Чужой wamid (OTP-канал того же номера) — 200 и никаких счётчиков.
    await _webhook(
        client,
        _status_payload(
            ctx.phone_number_id, "wamid.UNKNOWN", "sent", billable=True, category="utility"
        ),
    )
    row = await _usage(_resources, ctx.clinic_id)
    assert (row.wa_sent, row.wa_utility) == (1, 0)


# ── Админские ручки и смета ─────────────────────────────────────────────────


async def test_admin_usage_endpoints_estimate_cost_by_rate_card(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1 тыйын за токен запроса, 2 — за токен ответа, 300 — за utility-сообщение.
    monkeypatch.setattr(settings, "ai_rate_input_tiyin_per_mtok", 1_000_000)
    monkeypatch.setattr(settings, "ai_rate_output_tiyin_per_mtok", 2_000_000)
    monkeypatch.setattr(settings, "wa_rate_utility_tiyin", 300)

    await _set_mode(admin_client, ctx, "auto")
    fake.script = [
        AiResult(text=_reply_json("Здравствуйте!"), usage={"input": 1000, "output": 100})
    ]
    await _simulate(admin_client, ctx, "Салам", "+996700200404")
    wamid = await _outgoing(client, ctx, _resources, "+996700200405", "Напоминание")
    await _webhook(
        client,
        _status_payload(ctx.phone_number_id, wamid, "sent", billable=True, category="utility"),
    )

    period = datetime.now(UTC).strftime("%Y-%m")
    platform = await admin_client.get(f"{API}/admin/usage", params={"period": period})
    assert platform.status_code == 200, platform.text
    mine = next(c for c in platform.json()["clinics"] if c["clinicId"] == ctx.clinic_id)
    assert mine["totals"]["modelInputTokens"] == 1000
    assert mine["totals"]["waUtility"] == 1
    assert mine["estimatedCostTiyin"] == 1000 * 1 + 100 * 2 + 300
    assert platform.json()["rateCard"]["waUtility"] == 300

    clinic = await admin_client.get(f"{API}/admin/clients/{ctx.clinic_id}/usage")
    assert clinic.status_code == 200, clinic.text
    assert len(clinic.json()["days"]) == 1
    assert clinic.json()["totals"]["dialogs"] == 1
    assert clinic.json()["estimatedCostTiyin"] == 1500

    bad = await admin_client.get(
        f"{API}/admin/clients/{ctx.clinic_id}/usage",
        params={"dateFrom": "2026-09-10", "dateTo": "2026-09-01"},
    )
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_period"
