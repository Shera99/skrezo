"""Тесты устойчивости ассистента: пациент не должен оставаться без ответа.

Инцидент 22–31.10.2026 (разбор дампа прода): клиника неделю отвечала только
через одобрение администратора, четыре передачи человеку ушли без единого слова
пациенту, а пуш о передаче никто не увидел. Здесь — то, что должно было это
поймать или предотвратить:

* **проверка здоровья** опускает клиники из ``auto`` только после нескольких
  неудач подряд, сама же возвращает их обратно и не тратит квоту модели на
  пробу, когда о модели и так говорит живой трафик;
* **ждущий пациент** (нет ответа клиники дольше порога) — алерт команде, один
  на сообщение;
* **передача без ответа** сотрудника — одно напоминание владельцу и менеджеру.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fakeredis import aioredis
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.config import settings
from app.modules.ai.application.escalation_reminder import EscalationReminderService
from app.modules.ai.application.health import AiHealthService
from app.shared.ai import AiProviderError, AiResult, ToolCallRequest
from app.shared.ai.resilience import ALIVE_KEY, OPEN_KEY
from app.shared.db.enums import AiMessageRole, AiMode
from app.shared.db.models.ai_conversation import AiConversation
from app.shared.db.models.ai_escalation import AiEscalation
from app.shared.db.models.ai_message import AiMessage
from app.shared.db.models.clinic import Clinic
from app.shared.db.models.event import Event
from app.shared.whatsapp_cloud.logging_client import LoggingWhatsAppClient
from tests.api.conftest import Ctx, _FakeProvider, _reply_json, _set_mode, _simulate
from tests.conftest import API, Resources

_seq = iter(range(1, 10_000))
_BISHKEK = ZoneInfo("Asia/Bishkek")


def _patient() -> str:
    return f"+99670052{next(_seq):04d}"


@dataclass
class _DeadProvider:
    """Провайдер, который лёг: любой вызов — ошибка; считает обращения."""

    model_name: str = "dead-model"
    calls: int = 0

    async def complete(self, **_: Any) -> AiResult:
        self.calls += 1
        raise AiProviderError("провайдер недоступен")


@dataclass
class _TeamChat:
    """Чат команды Skrezo: что ему написали."""

    texts: list[str] = field(default_factory=list)

    async def notify_team(self, text: str, **_: Any) -> None:
        self.texts.append(text)


async def _health(
    res: Resources, provider: Any, redis: Any = None, team: _TeamChat | None = None
) -> Any:
    async with res.factory() as session:
        return await AiHealthService(
            session, provider, LoggingWhatsAppClient(), redis=redis, notifier=team
        ).check()


async def _mode(res: Resources, ctx: Ctx) -> tuple[AiMode, str | None]:
    async with res.factory() as session:
        clinic = await session.get(Clinic, uuid.UUID(ctx.clinic_id))
        assert clinic is not None
        return clinic.ai_mode, clinic.ai_mode_reason


async def _conversation_id(res: Resources, phone: str) -> uuid.UUID:
    async with res.factory() as session:
        return (
            await session.execute(select(AiConversation.id).where(AiConversation.wa_phone == phone))
        ).scalar_one()


async def _age_dialog(res: Resources, conversation_id: uuid.UUID, minutes: int) -> None:
    """Сдвинуть всю переписку и передачи диалога в прошлое на ``minutes``."""
    shift = timedelta(minutes=minutes)
    async with res.factory() as session:
        await session.execute(
            update(AiMessage)
            .where(AiMessage.conversation_id == conversation_id)
            .values(
                created_at=AiMessage.created_at - shift,
                sent_at=AiMessage.sent_at - shift,
            )
        )
        await session.execute(
            update(AiEscalation)
            .where(AiEscalation.conversation_id == conversation_id)
            .values(created_at=AiEscalation.created_at - shift)
        )
        await session.commit()


async def _working_hours(client: AsyncClient, ctx: Ctx, *, closed_today: bool) -> None:
    today = datetime.now(_BISHKEK).weekday()
    days = [
        {
            "weekday": weekday,
            "opensAt": "00:00:00",
            "closesAt": "23:59:59",
            "isWorking": not (closed_today and weekday == today),
        }
        for weekday in range(7)
    ]
    resp = await client.put(
        f"{API}/clinic/schedule/working-hours", headers=ctx.owner_headers, json={"days": days}
    )
    assert resp.status_code == 200, resp.text


async def _open_all_day(client: AsyncClient, ctx: Ctx) -> None:
    """Круглосуточная клиника: тихих часов нет, тесты не зависят от времени запуска."""
    await _working_hours(client, ctx, closed_today=False)


async def _closed_today(client: AsyncClient, ctx: Ctx) -> None:
    """Сегодня выходной — клиника в тихих часах весь день."""
    await _working_hours(client, ctx, closed_today=True)


# ── 1. Проверка здоровья: порог, возврат, живой трафик ──────────────────────


async def test_single_failed_check_does_not_downgrade(
    admin_client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Мигнувший провайдер не меняет режим: понижение — после N неудач подряд."""
    await _set_mode(admin_client, ctx, "auto")
    redis = aioredis.FakeRedis()
    dead = _DeadProvider()

    for attempt in range(1, settings.ai_health_downgrade_after):
        report = await _health(_resources, dead, redis)
        assert report.model_failures == attempt
        assert report.downgraded == 0
        assert (await _mode(_resources, ctx))[0] is AiMode.AUTO

    report = await _health(_resources, dead, redis)
    assert report.downgraded >= 1
    assert await _mode(_resources, ctx) == (AiMode.ASSISTED, "health")


async def test_recovered_model_restores_clinics_it_downgraded(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """Опустила проверка — проверка и вернёт, но после N здоровых проверок подряд.

    Одна здоровая проверка ничего не меняет: иначе провайдер с редкими 429
    гонял бы клиники туда-обратно. Команда узнаёт об обоих переходах.
    """
    await _set_mode(admin_client, ctx, "auto")
    redis = aioredis.FakeRedis()
    team = _TeamChat()
    for _ in range(settings.ai_health_downgrade_after):
        await _health(_resources, _DeadProvider(), redis, team)
    assert (await _mode(_resources, ctx))[0] is AiMode.ASSISTED

    for _ in range(settings.ai_health_downgrade_after - 1):
        fake.script = [AiResult(text="ок")]
        assert (await _health(_resources, fake, redis, team)).restored == 0
        assert (await _mode(_resources, ctx))[0] is AiMode.ASSISTED

    fake.script = [AiResult(text="ок")]
    report = await _health(_resources, fake, redis, team)

    assert report.restored >= 1
    assert await _mode(_resources, ctx) == (AiMode.AUTO, "health_restored")
    assert any("«Проверяю»" in t for t in team.texts)
    assert any("возвращено в auto" in t for t in team.texts)


async def test_owner_choosing_assisted_over_downgrade_is_not_undone(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Владелец явно выбрал «Проверяю» поверх понижения — это его решение.

    Раньше выбор текущей ступени был пустым действием: причина ``health``
    оставалась, и проверка через 15 минут возвращала клинику в auto вопреки
    владельцу.
    """
    await _set_mode(admin_client, ctx, "auto")
    redis = aioredis.FakeRedis()
    for _ in range(settings.ai_health_downgrade_after):
        await _health(_resources, _DeadProvider(), redis)
    assert await _mode(_resources, ctx) == (AiMode.ASSISTED, "health")

    chosen = await client.put(
        f"{API}/clinic/assistant/trust", headers=ctx.owner_headers, json={"level": "assisted"}
    )
    assert chosen.status_code == 200, chosen.text
    for _ in range(settings.ai_health_downgrade_after):
        fake.script = [AiResult(text="ок")]
        await _health(_resources, fake, redis)

    mode, reason = await _mode(_resources, ctx)
    assert mode is AiMode.ASSISTED
    assert reason != "health"


async def test_mode_set_by_human_is_never_restored(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, _resources: Resources
) -> None:
    """«Проверяю», выбранное человеком, живая модель не отменяет."""
    await _set_mode(admin_client, ctx, "assisted")
    fake.script = [AiResult(text="ок")]

    report = await _health(_resources, fake, aioredis.FakeRedis())

    assert report.restored == 0
    assert (await _mode(_resources, ctx))[0] is AiMode.ASSISTED


async def test_live_traffic_answers_for_the_model_without_probe(
    admin_client: AsyncClient, ctx: Ctx, _resources: Resources
) -> None:
    """Проба ест ту же квоту, что и пациенты: живой трафик говорит за модель сам.

    Недавний успешный вызов — модель жива; разомкнутый предохранитель — лежит.
    В обоих случаях пробного запроса нет.
    """
    await _set_mode(admin_client, ctx, "auto")
    dead = _DeadProvider()

    alive = aioredis.FakeRedis()
    await alive.set(ALIVE_KEY, "1")
    assert (await _health(_resources, dead, alive)).model_ok is True

    broken = aioredis.FakeRedis()
    await broken.set(OPEN_KEY, "1")
    assert (await _health(_resources, dead, broken)).model_ok is False

    assert dead.calls == 0


# ── 2. Пациент без ответа ───────────────────────────────────────────────────


async def test_waiting_patient_is_reported_to_team_once(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Ответ модели лёг черновиком, а пациент ждёт полчаса — алерт команде, один раз."""
    await _open_all_day(client, ctx)
    await _set_mode(admin_client, ctx, "assisted")
    phone = _patient()
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)  # черновик, не отправлен
    conversation_id = await _conversation_id(_resources, phone)
    await _age_dialog(_resources, conversation_id, settings.ai_unanswered_alert_minutes + 5)
    redis = aioredis.FakeRedis()
    team = _TeamChat()

    fake.script = [AiResult(text="ок")]
    first = await _health(_resources, fake, redis, team)
    fake.script = [AiResult(text="ок")]
    second = await _health(_resources, fake, redis, team)

    assert [(p.kind, p.conversation_id) for p in first.unanswered] == [("message", conversation_id)]
    assert first.unanswered[0].waited_minutes >= settings.ai_unanswered_alert_minutes
    assert second.unanswered == []
    assert any("ждут ответа" in t and phone[-4:] in t for t in team.texts)


async def test_waiting_is_counted_from_first_unanswered_message(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Пациент пишет «алло?» каждые 10 минут — ждёт он с ПЕРВОГО сообщения.

    Считай мы от последнего, самый встревоженный пациент не попадал бы в алерт
    никогда: его последнее сообщение всегда моложе порога.
    """
    await _open_all_day(client, ctx)
    await _set_mode(admin_client, ctx, "assisted")
    phone = _patient()
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"))]
    await _simulate(admin_client, ctx, "Здравствуйте", phone)
    conversation_id = await _conversation_id(_resources, phone)
    await _age_dialog(_resources, conversation_id, settings.ai_unanswered_alert_minutes + 5)
    fake.script = [AiResult(text=_reply_json("Слушаю вас"))]
    await _simulate(admin_client, ctx, "алло?", phone)  # свежее, тоже без ответа

    fake.script = [AiResult(text="ок")]
    report = await _health(_resources, fake)

    [waiting] = report.unanswered
    assert waiting.conversation_id == conversation_id
    assert waiting.waited_minutes >= settings.ai_unanswered_alert_minutes


async def test_handoff_without_staff_reply_is_reported(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Инцидент 22–31.10: «позвал администратора» ушло, а сотрудник молчит.

    Фраза передачи — ответ клиники, поэтому правило «сообщение без ответа» её
    не видит; нужна отдельная проверка передач.
    """
    await _open_all_day(client, ctx)
    conversation_id = await _escalated_dialog(admin_client, ctx, fake, _resources)
    await _age_dialog(_resources, conversation_id, settings.ai_unanswered_alert_minutes + 5)

    fake.script = [AiResult(text="ок")]
    report = await _health(_resources, fake)

    assert [(p.kind, p.conversation_id) for p in report.unanswered] == [
        ("handoff", conversation_id)
    ]


async def test_closed_clinic_is_not_reported_at_night(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Клиника закрыта — команду не будим: пациенту сказано, когда ответят."""
    await _closed_today(client, ctx)
    conversation_id = await _escalated_dialog(admin_client, ctx, fake, _resources)
    await _age_dialog(_resources, conversation_id, settings.ai_unanswered_alert_minutes + 5)

    fake.script = [AiResult(text="ок")]
    assert (await _health(_resources, fake)).unanswered == []


async def test_answered_or_shadow_patients_are_not_reported(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Ответ ушёл — пациент не ждёт; в теневом режиме отвечает человек — не наше дело."""
    await _open_all_day(client, ctx)
    await _set_mode(admin_client, ctx, "auto")
    answered = _patient()
    fake.script = [AiResult(text=_reply_json("Здравствуйте! Чем помочь?"))]
    await _simulate(admin_client, ctx, "Здравствуйте", answered)
    await _age_dialog(
        _resources,
        await _conversation_id(_resources, answered),
        settings.ai_unanswered_alert_minutes + 5,
    )

    fake.script = [AiResult(text="ок")]
    assert (await _health(_resources, fake)).unanswered == []

    await _set_mode(admin_client, ctx, "shadow")
    silent = _patient()
    fake.script = [AiResult(text=_reply_json("Здравствуйте!"))]
    await _simulate(admin_client, ctx, "Здравствуйте", silent)
    await _age_dialog(
        _resources,
        await _conversation_id(_resources, silent),
        settings.ai_unanswered_alert_minutes + 5,
    )

    fake.script = [AiResult(text="ок")]
    assert (await _health(_resources, fake)).unanswered == []


# ── 3. Напоминание о передаче без ответа ────────────────────────────────────


async def _escalated_dialog(
    admin_client: AsyncClient, ctx: Ctx, fake: _FakeProvider, res: Resources
) -> uuid.UUID:
    await _set_mode(admin_client, ctx, "auto")
    phone = _patient()
    fake.script = [
        AiResult(tool_call=ToolCallRequest(name="escalate", args={"reason": "patient_asked"}))
    ]
    body = await _simulate(admin_client, ctx, "Позовите администратора", phone)
    assert body["outcome"] == "escalated", body
    return await _conversation_id(res, phone)


async def _remind(res: Resources) -> list[uuid.UUID]:
    async with res.factory() as session:
        return await EscalationReminderService(session).run()


async def _reminders(res: Resources) -> int:
    async with res.factory() as session:
        return len(
            (
                await session.execute(
                    select(Event.id).where(Event.type == "ai.escalation_reminder")
                )
            ).all()
        )


async def test_unanswered_handoff_reminds_once(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Передача висит без ответа 30 минут — одно напоминание, не на каждом прогоне."""
    await _open_all_day(client, ctx)
    conversation_id = await _escalated_dialog(admin_client, ctx, fake, _resources)
    assert await _remind(_resources) == []  # ещё рано

    await _age_dialog(_resources, conversation_id, settings.ai_escalation_reminder_minutes + 5)

    assert await _remind(_resources) == [conversation_id]
    assert await _remind(_resources) == []
    assert await _reminders(_resources) == 1


async def test_reminder_waits_for_clinic_to_open(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Тихие часы напоминание не тратят: обычный пуш ночью не ставится вовсе.

    Раньше отметка «напомнили» ставилась и ночью — пуша не было, а утром
    напоминать было уже нечем.
    """
    await _closed_today(client, ctx)
    conversation_id = await _escalated_dialog(admin_client, ctx, fake, _resources)
    await _age_dialog(_resources, conversation_id, settings.ai_escalation_reminder_minutes + 5)

    assert await _remind(_resources) == []
    assert await _reminders(_resources) == 0

    await _open_all_day(client, ctx)  # клиника открылась
    assert await _remind(_resources) == [conversation_id]


async def test_no_reminder_when_staff_answered(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """Сотрудник уже написал пациенту — напоминать не о чем."""
    await _open_all_day(client, ctx)
    conversation_id = await _escalated_dialog(admin_client, ctx, fake, _resources)
    await _age_dialog(_resources, conversation_id, settings.ai_escalation_reminder_minutes + 5)
    async with _resources.factory() as session:
        session.add(
            AiMessage(
                conversation_id=conversation_id,
                clinic_id=uuid.UUID(ctx.clinic_id),
                role=AiMessageRole.ADMIN,
                body="Здравствуйте, это администратор",
                sent_at=datetime.now(UTC),
            )
        )
        await session.commit()

    assert await _remind(_resources) == []


async def test_no_reminder_when_staff_took_the_dialog(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
) -> None:
    """«Отвечаю сам» — сотрудник и так в курсе."""
    await _open_all_day(client, ctx)
    conversation_id = await _escalated_dialog(admin_client, ctx, fake, _resources)
    took = await client.post(
        f"{API}/clinic/conversations/{conversation_id}/assistant",
        headers=ctx.owner_headers,
        json={"active": False},
    )
    assert took.status_code == 200, took.text
    await _age_dialog(_resources, conversation_id, settings.ai_escalation_reminder_minutes + 5)

    assert await _remind(_resources) == []


async def test_reminders_can_be_switched_off(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    fake: _FakeProvider,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``AI_ESCALATION_REMINDER_MINUTES=0`` — напоминаний нет вовсе."""
    monkeypatch.setattr(settings, "ai_escalation_reminder_minutes", 0)
    await _open_all_day(client, ctx)
    conversation_id = await _escalated_dialog(admin_client, ctx, fake, _resources)
    await _age_dialog(_resources, conversation_id, 60)

    assert await _remind(_resources) == []
