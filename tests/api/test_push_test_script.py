"""Тесты ``make push-test`` (``app.scripts.push_test``) — проверка доставки пушей.

Ключевые риски:

* лог-фолбэк «доставляет» молча — отчёт не должен показать ✅, если живого
  канала нет (иначе проверка доставки врёт ровно там, где нужна);
* мёртвый токен и ошибка провайдера видны по каждому устройству отдельно, и
  одно сломанное устройство не мешает отправке на остальные;
* скрипт ничего не меняет в базе — мёртвое устройство остаётся на месте.

Провайдеры подменяются каналами-заглушками; скрипт открывает сессию сам, поэтому
``session_factory`` патчится в его модуле.
"""

from __future__ import annotations

import itertools
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.scripts import push_test
from app.shared.db.enums import DevicePlatform
from app.shared.db.models.account import Account
from app.shared.db.models.device import Device
from app.shared.push.channels import DeadTokenError, PushDeliveryError
from app.shared.push.logging_channel import LoggingPushChannel
from app.shared.push.message import PushMessage
from app.shared.push.transport import PushTransport
from tests.conftest import API, Resources

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070093{next(_phone_seq):04d}"


class _Channel:
    """Канал-заглушка: запоминает отправленное или падает заданной ошибкой."""

    def __init__(self, name: str, error: Exception | None = None) -> None:
        self.name = name
        self.sent: list[tuple[str, PushMessage]] = []
        self._error = error

    async def send(self, token: str, message: PushMessage) -> None:
        if self._error is not None:
            raise self._error
        self.sent.append((token, message))


@pytest.fixture(autouse=True)
def _script_db(_resources: Resources, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(push_test, "session_factory", _resources.factory)


async def _account_with_devices(
    admin_client: AsyncClient, res: Resources, platforms: list[DevicePlatform]
) -> str:
    """Клиника с владельцем и его устройствами; вернуть телефон владельца."""
    phone = _phone()
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец Пушей",
            "adminPhone": phone,
            "name": "Клиника Пушей",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    clinic_id = uuid.UUID(created.json()["id"])
    async with res.factory() as session:
        account = (
            await session.execute(select(Account).where(Account.phone == f"+996{phone[1:]}"))
        ).scalar_one()
        for platform in platforms:
            session.add(
                Device(
                    account_id=account.id,
                    clinic_id=clinic_id,
                    token=f"{platform.value}-{uuid.uuid4().hex}",
                    platform=platform,
                    app_version="1.0.0",
                )
            )
        await session.commit()
    return phone


async def test_sends_to_every_device_and_reports_each(
    admin_client: AsyncClient, _resources: Resources, capsys: pytest.CaptureFixture[str]
) -> None:
    """Каждое устройство — своим каналом, с отчётом; мёртвый токен не мешает остальным."""
    phone = await _account_with_devices(
        admin_client, _resources, [DevicePlatform.IOS, DevicePlatform.ANDROID]
    )
    apns = _Channel("apns", DeadTokenError("APNs: 400"))
    fcm = _Channel("fcm")
    transport = PushTransport({"ios": apns, "android": fcm}, LoggingPushChannel())

    code = await push_test.run(phone, transport=transport)

    out = capsys.readouterr().out
    assert code == 0
    assert len(fcm.sent) == 1
    message = fcm.sent[0][1]
    assert message.title == "Проверка уведомлений Skrezo"
    assert message.data["type"] == "test"
    assert message.android_channel == "schedule-changes.v1"
    assert "✅ FCM принял пуш" in out
    assert "💀 APNs" in out and "APNS_USE_SANDBOX" in out
    assert "Принято провайдером: 1 из 2" in out
    # Скрипт только смотрит: мёртвое устройство из базы не удалено.
    async with _resources.factory() as session:
        devices = (await session.execute(select(Device.platform))).scalars().all()
    assert DevicePlatform.IOS in devices


async def test_log_fallback_is_not_reported_as_delivered(
    admin_client: AsyncClient, _resources: Resources, capsys: pytest.CaptureFixture[str]
) -> None:
    """Живого канала нет — честное «ушёл в лог» и код 1, а не ✅."""
    phone = await _account_with_devices(admin_client, _resources, [DevicePlatform.ANDROID])

    code = await push_test.run(phone, transport=PushTransport({}, LoggingPushChannel()))

    out = capsys.readouterr().out
    assert code == 1
    assert "✅" not in out
    assert "пуш ушёл только в лог" in out
    assert "Ни одно устройство не приняло пуш" in out


async def test_provider_error_is_shown_with_reason(
    admin_client: AsyncClient, _resources: Resources, capsys: pytest.CaptureFixture[str]
) -> None:
    """Временная ошибка провайдера видна с причиной; код 1 — никто не принял."""
    phone = await _account_with_devices(admin_client, _resources, [DevicePlatform.ANDROID])
    transport = PushTransport(
        {"android": _Channel("fcm", PushDeliveryError("FCM: HTTP 403"))}, LoggingPushChannel()
    )

    code = await push_test.run(phone, transport=transport)

    assert code == 1
    assert "❌ FCM: HTTP 403" in capsys.readouterr().out


async def test_account_without_devices_explains_what_to_do(
    admin_client: AsyncClient, _resources: Resources, capsys: pytest.CaptureFixture[str]
) -> None:
    """Устройств нет — подсказка войти в приложение и разрешить уведомления."""
    phone = await _account_with_devices(admin_client, _resources, [])

    code = await push_test.run(phone, transport=PushTransport({}, LoggingPushChannel()))

    assert code == 1
    assert "нет зарегистрированных устройств" in capsys.readouterr().out


async def test_unknown_phone_and_garbage_input(capsys: pytest.CaptureFixture[str]) -> None:
    """Номера нет в базе или это не номер — понятная ошибка, код 1."""
    assert await push_test.run("0700999999") == 1
    assert "Аккаунта с номером +996700999999 нет" in capsys.readouterr().out

    assert await push_test.run("123") == 1
    assert "Некорректный номер" in capsys.readouterr().out
