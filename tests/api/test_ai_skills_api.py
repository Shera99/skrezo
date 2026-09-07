"""Ф7, инкремент 3: матрица умений — клиника × умение вместо одного режима.

Ступень режет сверху, потолок умения — снизу: клиника в «Доверяю» может
отвечать сама, но запись оставить за администратором (assisted) или
выключить (off). Выключенное умение исчезает из инструментов и правил;
assisted заменяет кнопку эскалацией с карточкой.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.shared.ai import AiResult, ToolCallRequest
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.slot_hold import SlotHold
from tests.api.conftest import Ctx, _FakeProvider, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources, own_membership_id


def _workday_ten() -> str:
    day = datetime.now(UTC).date() + timedelta(days=1)
    if day.weekday() == 6:
        day += timedelta(days=1)
    return f"{day.isoformat()}T10:00:00+06:00"


async def _ladder(client: AsyncClient, ctx: Ctx) -> Any:
    resp = await client.get(f"{API}/clinic/assistant/trust", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _set_skills(client: AsyncClient, ctx: Ctx, **caps: str) -> Any:
    resp = await client.put(
        f"{API}/clinic/assistant/trust", headers=ctx.owner_headers, json={"skills": caps}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _skills(ladder: Any) -> dict[str, tuple[str, str]]:
    return {s["key"]: (s["cap"], s["effective"]) for s in ladder["skills"]}


def _tool_names(fake: _FakeProvider, call: int = -1) -> set[str]:
    return {t.name for t in fake.calls[call]["tools"]}


async def test_effective_level_is_min_of_ladder_and_cap(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx
) -> None:
    ladder = await _ladder(client, ctx)
    assert _skills(ladder) == {
        "booking": ("auto", "off"),
        "changes": ("auto", "off"),
        "reminders": ("off", "off"),
        "debts": ("off", "off"),
        "recall": ("off", "off"),
    }  # бот выключен

    await _set_mode(admin_client, ctx, "assisted")
    assert _skills(await _ladder(client, ctx))["booking"] == ("auto", "assisted")

    await _set_mode(admin_client, ctx, "auto")
    assert _skills(await _ladder(client, ctx))["booking"] == ("auto", "auto")

    updated = await _set_skills(client, ctx, booking="assisted", changes="off", reminders="auto")
    assert _skills(updated) == {
        "booking": ("assisted", "assisted"),
        "changes": ("off", "off"),
        "reminders": ("auto", "auto"),
        "debts": ("off", "off"),
        "recall": ("off", "off"),
    }

    await _set_mode(admin_client, ctx, "shadow")  # ступень ниже потолка — режет ступень
    assert _skills(await _ladder(client, ctx)) == {
        "booking": ("assisted", "shadow"),
        "changes": ("off", "off"),
        "reminders": ("auto", "shadow"),
        "debts": ("off", "off"),
        "recall": ("off", "off"),
    }

    bad = await client.put(
        f"{API}/clinic/assistant/trust",
        headers=ctx.owner_headers,
        json={"skills": {"booking": "always"}},
    )
    assert bad.status_code == 422 and bad.json()["error"] == "skill_unknown"


async def test_disabled_booking_hides_tools_and_rewrites_rules(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    await _set_skills(client, ctx, booking="off")
    doctor_id = await own_membership_id(client, ctx.owner_headers)

    # Модель «вспомнила» инструмент по истории — вторая линия отказывает данными.
    fake.script = [
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment", args={"doctorId": doctor_id, "startsAt": _workday_ten()}
            )
        ),
        AiResult(text=_reply_json("Запись оформит администратор.", needs_human=True)),
    ]
    body = await _simulate(admin_client, ctx, "Запишите меня на завтра", "+996700800500")
    assert body["outcome"] == "escalated"

    system = fake.calls[0]["system"]
    assert "ЗАПИСЬ ЧЕРЕЗ БОТА ОТКЛЮЧЕНА" in system and "КАК ВЕСТИ ЗАПИСЬ" not in system
    assert "ПЕРЕНОС И ОТМЕНА ЗАПИСИ" in system  # второе умение живо
    names = _tool_names(fake, 0)
    assert {"propose_appointment", "create_patient"}.isdisjoint(names)
    assert {"get_available_slots", "propose_cancel", "get_services"} <= names
    tool_part = next(p for p in fake.calls[1]["parts"] if p.role == "tool")
    assert "отключён" in tool_part.tool_result["error"]
    async with _resources.factory() as session:
        assert (await session.execute(select(SlotHold))).scalars().all() == []

    # Оба умения off — уходят и общие инструменты подбора времени.
    await _set_skills(client, ctx, changes="off")
    fake.script = [AiResult(text=_reply_json("Чем могу помочь?"))]
    await _simulate(admin_client, ctx, "Здравствуйте", "+996700800501")
    assert {"get_available_days", "get_available_slots"}.isdisjoint(_tool_names(fake))
    assert "ПЕРЕНОС И ОТМЕНА ЧЕРЕЗ БОТА ОТКЛЮЧЕНЫ" in fake.calls[-1]["system"]


async def test_assisted_booking_hands_card_to_admin_instead_of_button(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    await _set_mode(admin_client, ctx, "auto")
    await _set_skills(client, ctx, booking="assisted")
    doctor_id = await own_membership_id(client, ctx.owner_headers)

    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="create_patient", args={"firstName": "Айбек"})),
        AiResult(
            tool_call=ToolCallRequest(
                name="propose_appointment", args={"doctorId": doctor_id, "startsAt": _workday_ten()}
            )
        ),
        AiResult(text=_reply_json("Администратор подтвердит запись.")),
    ]
    body = await _simulate(
        admin_client, ctx, "Запишите меня, я Айбек, завтра в 10", "+996700800510"
    )
    assert body["outcome"] == "escalated"
    assert body["escalationReason"] == "booking_needs_human"
    assert "кнопка подтверждения пациенту НЕ уходит" in fake.calls[0]["system"]
    assert len(fake.calls) == 2  # после карточки ход модели не нужен — диалог у человека

    async with _resources.factory() as session:
        hold = (await session.execute(select(SlotHold))).scalar_one()
        assert hold.kind == "book" and hold.consumed_at is None
        escalation = (
            await session.execute(
                select(AiEscalation).where(AiEscalation.clinic_id == uuid.UUID(ctx.clinic_id))
            )
        ).scalar_one()
        assert escalation.reason == "booking_needs_human"
        note = str((escalation.payload or {}).get("note"))
        assert note.startswith("Приём, врач ") and "10:00" in note
    # Кнопки не было: сообщений от системы с «Проверьте запись» нет.
    assert not any("Проверьте запись" in (m["body"] or "") for m in body["messages"])
