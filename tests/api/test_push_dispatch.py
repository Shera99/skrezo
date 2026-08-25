"""Тесты диспетчера и эмиттера пушей (P0-b, инкремент 1: sync.day).

Транспорт настоящий не зовём (кредов нет и они непроверяемы) — вместо канала
подставляем записывающий фейк. Проверяем то, что и есть логика диспетчера:

* фан-аут sync.day на ВСЕ устройства клиники;
* sync.day — тихий (без заголовка), но с ``data`` (type/id/clinicId);
* мёртвый токен (``DeadTokenError``) удаляется, остальным доставка идёт;
* видимое событие рендерится текстом на языке КАЖДОГО устройства;
* эмиттер ставит задачу в очередь, а при недоступной очереди — молча пропускает.
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.jobs import DISPATCH_PUSH
from app.modules.push.application.dispatcher import PushDispatcher
from app.modules.push.application.emitter import PushEmitter
from app.modules.push.infrastructure.repositories.device_repository import DeviceRepository
from app.shared.db.models.device import Device
from app.shared.push.channels import DeadTokenError
from app.shared.push.message import PushMessage
from app.shared.push.transport import PushTransport
from tests.conftest import API, Resources, otp_login

_phone_seq = itertools.count(1)
_token_seq = itertools.count(1)


def _phone() -> str:
    return f"070077{next(_phone_seq):04d}"


def _token() -> str:
    return f"disp-tok-{next(_token_seq):06d}"


class _RecordingChannel:
    """Фейковый канал: записывает доставленное; на «мёртвые» токены — DeadTokenError."""

    name = "rec"

    def __init__(self, dead: set[str] | None = None) -> None:
        self.sent: list[tuple[str, PushMessage]] = []
        self._dead = dead or set()

    async def send(self, token: str, message: PushMessage) -> None:
        if token in self._dead:
            raise DeadTokenError("gone")
        self.sent.append((token, message))


@pytest.fixture
async def owner(admin_client: AsyncClient, client: AsyncClient) -> dict[str, str]:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Диспетчер",
            "adminPhone": phone,
            "name": "Клиника Диспетчер",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    return await otp_login(client, phone)


async def _register(client: AsyncClient, owner: dict[str, str], token: str, **extra: str) -> None:
    resp = await client.post(f"{API}/clinic/devices", headers=owner, json={"token": token, **extra})
    assert resp.status_code == 200, resp.text


async def _clinic_id(res: Resources, token: str) -> uuid.UUID:
    async with res.factory() as session:
        return (
            await session.execute(select(Device.clinic_id).where(Device.token == token))
        ).scalar_one()


# ── Диспетчер ────────────────────────────────────────────────────────────────


async def test_sync_day_fans_out_to_all_clinic_devices(
    client: AsyncClient, owner: dict[str, str], _resources: Resources
) -> None:
    """sync.day уходит на все устройства клиники, тихо и с data."""
    ta, tb = _token(), _token()
    await _register(client, owner, ta, platform="android", locale="ru")
    await _register(client, owner, tb, platform="ios", locale="kg")
    clinic_id = await _clinic_id(_resources, ta)

    channel = _RecordingChannel()
    async with _resources.factory() as session:
        dispatcher = PushDispatcher(session, PushTransport({}, channel), DeviceRepository(session))
        sent = await dispatcher.dispatch("sync.day", clinic_id)

    assert sent == 2
    assert {t for t, _ in channel.sent} == {ta, tb}
    for _, message in channel.sent:
        assert message.is_silent and message.title is None  # тихий data-only
        assert message.data["type"] == "sync.day"
        assert message.data["clinicId"] == str(clinic_id)
        assert message.data["id"].startswith("evt_")


async def test_dispatch_deletes_dead_token(
    client: AsyncClient, owner: dict[str, str], _resources: Resources
) -> None:
    """Мёртвый токен снимается, остальным доставка не срывается."""
    ta, tb = _token(), _token()
    await _register(client, owner, ta, platform="android")
    await _register(client, owner, tb, platform="android")
    clinic_id = await _clinic_id(_resources, tb)

    channel = _RecordingChannel(dead={ta})
    async with _resources.factory() as session:
        dispatcher = PushDispatcher(session, PushTransport({}, channel), DeviceRepository(session))
        sent = await dispatcher.dispatch("sync.day", clinic_id)

    assert sent == 1
    assert {t for t, _ in channel.sent} == {tb}
    async with _resources.factory() as session:
        left = (
            (await session.execute(select(Device.token).where(Device.clinic_id == clinic_id)))
            .scalars()
            .all()
        )
    assert ta not in left and tb in left  # мёртвый удалён, живой на месте


async def test_dispatch_renders_text_per_device_locale(
    client: AsyncClient, owner: dict[str, str], _resources: Resources
) -> None:
    """Видимое событие рендерится на языке каждого устройства с подстановкой."""
    ru_token, kg_token = _token(), _token()
    await _register(client, owner, ru_token, platform="android", locale="ru")
    await _register(client, owner, kg_token, platform="ios", locale="kg")
    clinic_id = await _clinic_id(_resources, ru_token)

    channel = _RecordingChannel()
    async with _resources.factory() as session:
        dispatcher = PushDispatcher(session, PushTransport({}, channel), DeviceRepository(session))
        sent = await dispatcher.dispatch(
            "day.morning", clinic_id, params={"count": "3", "time": "09:00"}
        )

    assert sent == 2
    by_token = dict(channel.sent)
    assert by_token[ru_token].title == "Ваш день"
    assert "3" in (by_token[ru_token].body or "")
    assert by_token[kg_token].title == "Бүгүнкү күнүңүз"
    assert not by_token[ru_token].is_silent  # у видимого события заголовок есть


async def test_dispatch_no_devices_is_noop(_resources: Resources) -> None:
    """Клиника без устройств — ноль отправок, без ошибок."""
    channel = _RecordingChannel()
    async with _resources.factory() as session:
        dispatcher = PushDispatcher(session, PushTransport({}, channel), DeviceRepository(session))
        sent = await dispatcher.dispatch("sync.day", uuid.uuid4())
    assert sent == 0
    assert channel.sent == []


# ── Эмиттер ──────────────────────────────────────────────────────────────────


class _FakeQueue:
    """Фейковая очередь: пишет постановки, отдаёт заданный результат enqueue."""

    def __init__(self, *, ok: bool) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._ok = ok

    async def enqueue(self, name: str, *args: object, **_: object) -> bool:
        self.calls.append((name, args))
        return self._ok


async def test_emitter_enqueues_dispatch_push() -> None:
    """emit_sync_day ставит задачу dispatch_push с типом и id клиники."""
    queue = _FakeQueue(ok=True)
    clinic_id = uuid.uuid4()
    await PushEmitter(queue).emit_sync_day(clinic_id)  # type: ignore[arg-type]
    assert queue.calls == [(DISPATCH_PUSH, ("sync.day", str(clinic_id)))]


async def test_emitter_skips_when_queue_unavailable() -> None:
    """Очередь недоступна (тесты/Redis лёг) — sync.day молча пропускается."""
    queue = _FakeQueue(ok=False)
    await PushEmitter(queue).emit_sync_day(uuid.uuid4())  # type: ignore[arg-type]
    assert len(queue.calls) == 1  # попытка была, но результат False — без падения
