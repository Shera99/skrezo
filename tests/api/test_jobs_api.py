"""Тесты фоновых задач: журнал запусков, состояние воркера и сами джобы.

Ключевые риски:

* журнал должен переживать падение задачи — иначе исчезает ровно та запись,
  ради которой он и заведён;
* предупреждение о конце триала обязано быть однократным: джоба ходит каждый
  день, а владельцу «осталось 3 дня» нужно сказать один раз;
* очередь недоступна — сообщение не теряется, а уходит на месте;
* состояние воркера должно отличать «встал» от «просто нечего делать».

Воркер работает своей сессией (``session_factory``), а не зависимостью FastAPI,
поэтому в тестах она подменяется на тестовую — см. :func:`_worker_db`.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from arq import Retry
from httpx import AsyncClient
from sqlalchemy import select

from app.core.jobs import WORKER_HEARTBEAT_KEY, JobQueue
from app.jobs.tasks import maintenance, notifications
from app.jobs.tasks import subscriptions as subscription_tasks
from app.modules.jobs.application.services import job_recorder
from app.modules.jobs.application.services.job_recorder import JobRunRecorder
from app.shared.db.enums import JobRunStatus, SubscriptionStatus
from app.shared.db.models.job_run import JobRun
from app.shared.db.models.subscription import Subscription
from app.shared.notifications.channels import MessageDeliveryError
from app.shared.notifications.delivery import TextDeliveryService
from app.shared.notifications.notifier import Notifier
from tests.conftest import API, Resources

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070095{next(_phone_seq):04d}"


class _Recorder:
    """Канал-«записная книжка» (тот же приём, что в тестах уведомлений)."""

    name = "recorder"

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self._fail = fail

    async def send_text(self, phone: str, text: str) -> None:
        self.sent.append((phone, text))
        if self._fail:
            raise MessageDeliveryError("recorder: сломан")


class _ChatRecorder:
    name = "chat-recorder"

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def _worker_db(_resources: Resources, monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменить фабрику сессий воркера на тестовую.

    Джобы и журнал открывают сессию сами (у фоновой задачи нет запроса, а значит
    и зависимостей FastAPI), поэтому оверрайд ``get_session`` их не касается.
    Подменяем имя в каждом модуле, который его импортировал: ``from ... import``
    связывает объект, и патч самого ``app.core.db`` до них бы не дошёл.
    """
    monkeypatch.setattr(subscription_tasks, "session_factory", _resources.factory)
    monkeypatch.setattr(maintenance, "session_factory", _resources.factory)
    monkeypatch.setattr(job_recorder, "session_factory", _resources.factory)


def _capture_notifications(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_Recorder, _ChatRecorder]:
    """Заставить джобы слать в записные книжки вместо провайдеров."""
    phones, chat = _Recorder(), _ChatRecorder()
    monkeypatch.setattr(
        subscription_tasks,
        "notifier",
        lambda ctx: Notifier(JobQueue(None), TextDeliveryService([phones]), chat),
    )
    return phones, chat


async def _job_runs(res: Resources, job_name: str) -> list[JobRun]:
    async with res.factory() as session:
        rows = await session.execute(
            select(JobRun).where(JobRun.job_name == job_name).order_by(JobRun.started_at)
        )
        return list(rows.scalars())


async def _create_clinic(admin_client: AsyncClient, phone: str, name: str) -> str:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец",
            "adminPhone": phone,
            "name": name,
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    clinic_id: str = created.json()["id"]
    return clinic_id


async def _set_trial_end(res: Resources, clinic_id: str, ends_at: datetime) -> None:
    async with res.factory() as session:
        subscription = (
            await session.execute(
                select(Subscription).where(Subscription.clinic_id == clinic_id)
            )
        ).scalar_one()
        subscription.trial_ends_at = ends_at
        await session.commit()


# ── Журнал запусков ─────────────────────────────────────────────────────────


async def test_recorder_writes_start_and_result(
    _resources: Resources, _worker_db: None
) -> None:
    """Успешный запуск: строка открывается и закрывается результатом."""
    async with JobRunRecorder("demo_job") as run:
        run.note("сделано: 2")

    runs = await _job_runs(_resources, "demo_job")
    assert len(runs) == 1
    assert runs[0].status is JobRunStatus.OK
    assert runs[0].detail == "сделано: 2"
    assert runs[0].finished_at is not None


async def test_recorder_survives_failing_job(
    _resources: Resources, _worker_db: None
) -> None:
    """Падение задачи журнал не откатывает и наружу пропускает."""
    with pytest.raises(ValueError, match="сломалось"):
        async with JobRunRecorder("demo_job", 2) as run:
            run.note("дошли до провайдера")
            raise ValueError("сломалось")

    runs = await _job_runs(_resources, "demo_job")
    assert len(runs) == 1
    assert runs[0].status is JobRunStatus.FAILED
    assert runs[0].attempt == 2
    # И заметка задачи, и причина падения: у Retry в тексте только задержка.
    assert "дошли до провайдера" in (runs[0].detail or "")
    assert "сломалось" in (runs[0].detail or "")


# ── Джобы уведомлений ───────────────────────────────────────────────────────


async def test_send_notification_job_delivers(
    _resources: Resources, _worker_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Задача доставки шлёт текст и отмечается в журнале."""
    recorder = _Recorder()
    monkeypatch.setattr(notifications, "build_text_channels", lambda: (recorder,))

    result = await notifications.send_notification({}, "+996700123456", "текст", "test_event")

    assert result == "sent"
    assert recorder.sent == [("+996700123456", "текст")]
    runs = await _job_runs(_resources, "send_notification")
    assert runs[0].status is JobRunStatus.OK


async def test_send_notification_job_retries_on_failure(
    _resources: Resources, _worker_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Недоставка — повод повторить, а не признать успех."""
    monkeypatch.setattr(notifications, "build_text_channels", lambda: (_Recorder(fail=True),))

    with pytest.raises(Retry):
        await notifications.send_notification(
            {"job_try": 2}, "+996700123456", "текст", "test_event"
        )

    runs = await _job_runs(_resources, "send_notification")
    assert runs[0].status is JobRunStatus.FAILED
    assert runs[0].attempt == 2
    assert "сломан" in (runs[0].detail or "")


# ── Подписки ────────────────────────────────────────────────────────────────


async def test_expire_trials_notifies_owner_and_team(
    admin_client: AsyncClient,
    _resources: Resources,
    _worker_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Истёкший триал переводится в режим чтения, владелец и команда узнают."""
    phones, chat = _capture_notifications(monkeypatch)
    phone = _phone()
    clinic_id = await _create_clinic(admin_client, phone, "Клиника Истёкшая")
    await _set_trial_end(_resources, clinic_id, datetime.now(UTC) - timedelta(days=1))

    count = await subscription_tasks.expire_trials({})

    assert count == 1
    assert len(phones.sent) == 1
    assert "Клиника Истёкшая" in phones.sent[0][1]
    assert len(chat.sent) == 1
    assert "Клиника Истёкшая" in chat.sent[0]

    async with _resources.factory() as session:
        subscription = (
            await session.execute(
                select(Subscription).where(Subscription.clinic_id == clinic_id)
            )
        ).scalar_one()
        assert subscription.status is SubscriptionStatus.TRIAL_EXPIRED


async def test_expire_trials_is_idempotent(
    admin_client: AsyncClient,
    _resources: Resources,
    _worker_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повторный прогон никого не трогает второй раз и не пишет повторно."""
    phones, _ = _capture_notifications(monkeypatch)
    clinic_id = await _create_clinic(admin_client, _phone(), "Клиника Дубль")
    await _set_trial_end(_resources, clinic_id, datetime.now(UTC) - timedelta(days=1))

    assert await subscription_tasks.expire_trials({}) == 1
    assert await subscription_tasks.expire_trials({}) == 0
    assert len(phones.sent) == 1


async def test_trial_warning_is_sent_once(
    admin_client: AsyncClient,
    _resources: Resources,
    _worker_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«Триал кончается» — один раз, сколько бы дней джоба ни ходила."""
    phones, chat = _capture_notifications(monkeypatch)
    clinic_id = await _create_clinic(admin_client, _phone(), "Клиника Предупреждённая")
    await _set_trial_end(_resources, clinic_id, datetime.now(UTC) + timedelta(days=2))

    assert await subscription_tasks.warn_trial_ending({}) == 1
    assert await subscription_tasks.warn_trial_ending({}) == 0

    assert len(phones.sent) == 1
    assert "Клиника Предупреждённая" in phones.sent[0][1]
    assert len(chat.sent) == 1


async def test_trial_warning_ignores_distant_and_expired(
    admin_client: AsyncClient,
    _resources: Resources,
    _worker_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Далёкий триал предупреждать рано, истёкший — уже поздно."""
    _capture_notifications(monkeypatch)
    distant = await _create_clinic(admin_client, _phone(), "Клиника Далёкая")
    await _set_trial_end(_resources, distant, datetime.now(UTC) + timedelta(days=20))
    past = await _create_clinic(admin_client, _phone(), "Клиника Просроченная")
    await _set_trial_end(_resources, past, datetime.now(UTC) - timedelta(hours=1))

    assert await subscription_tasks.warn_trial_ending({}) == 0


# ── Уборка журнала ──────────────────────────────────────────────────────────


async def test_cleanup_removes_only_old_runs(
    _resources: Resources, _worker_db: None
) -> None:
    """Уборка сносит старое и не трогает свежее."""
    async with JobRunRecorder("demo_job") as run:
        run.note("свежая")
    async with _resources.factory() as session:
        session.add(
            JobRun(
                job_name="demo_job",
                status=JobRunStatus.OK,
                attempt=1,
                started_at=datetime.now(UTC) - timedelta(days=400),
            )
        )
        await session.commit()

    deleted = await maintenance.cleanup_job_runs({})

    assert deleted == 1
    remaining = await _job_runs(_resources, "demo_job")
    assert [r.detail for r in remaining] == ["свежая"]


# ── Состояние воркера в админке ─────────────────────────────────────────────


async def test_worker_status_reports_dead_worker(
    admin_client: AsyncClient, _resources: Resources, _worker_db: None
) -> None:
    """Отметки жизни нет — воркер считается остановленным."""
    async with JobRunRecorder("expire_trials") as run:
        run.note("переведено клиник: 0")

    resp = await admin_client.get(f"{API}/admin/jobs")

    assert resp.status_code == 200, resp.text
    assert resp.json()["alive"] is False
    assert resp.json()["lastHeartbeatAt"] is None
    names = [r["jobName"] for r in resp.json()["lastRuns"]]
    assert names == ["expire_trials"]


async def test_worker_status_reports_alive_worker(
    admin_client: AsyncClient, _resources: Resources
) -> None:
    """Отметка на месте — воркер жив, время видно."""
    moment = datetime.now(UTC).isoformat()
    await _resources.fake.set(WORKER_HEARTBEAT_KEY, moment, ex=180)

    resp = await admin_client.get(f"{API}/admin/jobs")

    assert resp.status_code == 200, resp.text
    assert resp.json()["alive"] is True
    assert resp.json()["lastHeartbeatAt"] is not None


async def test_job_runs_journal_filters_and_counts(
    admin_client: AsyncClient, _resources: Resources, _worker_db: None
) -> None:
    """Журнал фильтруется по имени задачи и считает общее число записей."""
    async with JobRunRecorder("expire_trials") as run:
        run.note("переведено клиник: 0")
    with pytest.raises(ValueError, match="упс"):
        async with JobRunRecorder("send_notification"):
            raise ValueError("упс")

    everything = await admin_client.get(f"{API}/admin/jobs/runs")
    assert everything.status_code == 200, everything.text
    assert everything.json()["total"] == 2

    filtered = await admin_client.get(f"{API}/admin/jobs/runs", params={"job": "expire_trials"})
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["status"] == "ok"
    assert filtered.json()["items"][0]["durationMs"] is not None

    failed = await admin_client.get(f"{API}/admin/jobs/runs", params={"job": "send_notification"})
    assert failed.json()["items"][0]["status"] == "failed"
    assert "упс" in failed.json()["items"][0]["detail"]


@pytest.mark.parametrize("path", ["/admin/jobs", "/admin/jobs/runs"])
async def test_jobs_endpoints_need_platform_admin(client: AsyncClient, path: str) -> None:
    """Состояние воркера — не для клиник: без токена Skrezo хода нет."""
    resp = await client.get(f"{API}{path}")
    assert resp.status_code in (401, 403), resp.text


# ── Очередь ─────────────────────────────────────────────────────────────────


async def test_queue_without_pool_reports_failure() -> None:
    """Пула нет — очередь честно говорит «не поставил», а не молчит."""
    assert await JobQueue(None).enqueue("any_job", 1) is False


async def test_notifier_delivers_inline_when_queue_is_down() -> None:
    """Очередь недоступна — сообщение уходит на месте, а не теряется."""
    recorder = _Recorder()

    await Notifier(JobQueue(None), TextDeliveryService([recorder])).notify_phone(
        "+996700123456", "текст", event="test"
    )

    assert recorder.sent == [("+996700123456", "текст")]


async def test_notifier_uses_queue_when_available() -> None:
    """Очередь на месте — доставка уходит в неё, а не в обработчик запроса."""
    recorder = _Recorder()
    enqueued: list[tuple[str, tuple[Any, ...]]] = []

    class _Queue(JobQueue):
        def __init__(self) -> None:
            super().__init__(None)

        async def enqueue(self, name: str, *args: Any, **kwargs: Any) -> bool:
            enqueued.append((name, args))
            return True

    await Notifier(_Queue(), TextDeliveryService([recorder])).notify_phone(
        "+996700123456", "текст", event="test"
    )

    assert recorder.sent == []  # обработчик запроса провайдера не ждал
    assert enqueued == [("send_notification", ("+996700123456", "текст", "test"))]
