"""Юнит-тесты живых каналов пушей (FCM HTTP v1, APNs token-based).

Сеть не зовётся: httpx.MockTransport перехватывает запросы, ключи подписи
генерируются на лету. Проверяется то, что нельзя проверить на живом проде:

* FCM: OAuth-grant сервис-аккаунта c кешем токена; форма message (data —
  строками, notification, android.priority/ttl/channel_id/collapse);
* APNs: заголовки (topic, priority, collapse-id ≤ 64, expiration), тело
  (aps.alert/badge/thread-id/time-sensitive + data на верхнем уровне);
* мёртвые токены обоих провайдеров → DeadTokenError (устройство снимается);
* фабрика: креды включают живые каналы, их отсутствие — лог-фолбэк.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from app.core.config import settings
from app.shared.push.apns import ApnsChannel
from app.shared.push.channels import DeadTokenError, PushDeliveryError
from app.shared.push.factory import build_push_transport
from app.shared.push.fcm import FcmChannel
from app.shared.push.message import PushMessage


def _pem(private_key: Any) -> str:
    pem: bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


def _service_account_json() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "skrezo-test",
            "private_key": _pem(key),
            "client_email": "fcm@test.iam.gserviceaccount.com",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )


def _ec_key_pem() -> str:
    return _pem(ec.generate_private_key(ec.SECP256R1()))


def _message(**overrides: Any) -> PushMessage:
    base: dict[str, Any] = {
        "data": {"type": "appointment.created", "id": "evt_1", "clinicId": "c1"},
        "title": "Новая запись",
        "body": "Аружан — 02.09 10:00",
        "collapse_key": "appointment:a1",
        "urgent": True,
        "android_channel": "schedule-changes.v1",
        "ttl_seconds": 1800,
        "badge": 3,
        "thread_id": "day:2026-09-02",
    }
    base.update(overrides)
    return PushMessage(**base)


# ── FCM ─────────────────────────────────────────────────────────────────────


def _fcm_channel(handler: Any) -> FcmChannel:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FcmChannel(_service_account_json(), client=client)


async def test_fcm_sends_v1_message_and_caches_oauth() -> None:
    """Первый send делает OAuth-grant, второй — нет; форма message по HTTP v1."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            assert b"jwt-bearer" in request.content
            return httpx.Response(200, json={"access_token": "at-1", "expires_in": 3600})
        assert request.url.path == "/v1/projects/skrezo-test/messages:send"
        assert request.headers["Authorization"] == "Bearer at-1"
        return httpx.Response(200, json={"name": "projects/skrezo-test/messages/1"})

    channel = _fcm_channel(handler)
    await channel.send("tok-1", _message())
    await channel.send("tok-2", _message())

    oauth_calls = [c for c in calls if c.url.host == "oauth2.googleapis.com"]
    assert len(oauth_calls) == 1  # токен закеширован

    sent = json.loads(calls[-1].content)["message"]
    assert sent["token"] == "tok-2"
    assert sent["data"]["type"] == "appointment.created"
    assert sent["notification"] == {"title": "Новая запись", "body": "Аружан — 02.09 10:00"}
    android = sent["android"]
    assert android["priority"] == "HIGH"
    assert android["collapse_key"] == "appointment:a1"
    assert android["ttl"] == "1800s"
    assert android["notification"]["channel_id"] == "schedule-changes.v1"
    assert android["notification"]["notification_count"] == 3


async def test_fcm_dead_token() -> None:
    """404/UNREGISTERED — токен мёртв: DeadTokenError, а не ретраи."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        return httpx.Response(
            404, json={"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}}
        )

    with pytest.raises(DeadTokenError):
        await _fcm_channel(handler).send("dead", _message())


async def test_fcm_server_error_is_retryable() -> None:
    """5xx — временная ошибка: PushDeliveryError (не DeadToken)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        return httpx.Response(503, text="unavailable")

    with pytest.raises(PushDeliveryError) as err:
        await _fcm_channel(handler).send("tok", _message())
    assert not isinstance(err.value, DeadTokenError)


# ── APNs ────────────────────────────────────────────────────────────────────


def _apns_channel(handler: Any, **kwargs: Any) -> ApnsChannel:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ApnsChannel(
        _ec_key_pem(),
        "Y5HZFHJFCK",
        "TEAM123456",
        "kg.skrezo.app",
        use_sandbox=kwargs.get("use_sandbox", True),
        client=client,
    )


async def test_apns_headers_and_body() -> None:
    """Заголовки и aps-структура — по правилам APNs, data — на верхнем уровне."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    await _apns_channel(handler).send("ios-tok", _message(collapse_key="x" * 100))

    req = seen[0]
    assert req.url.host == "api.sandbox.push.apple.com"
    assert req.url.path == "/3/device/ios-tok"
    assert req.headers["apns-topic"] == "kg.skrezo.app"
    assert req.headers["apns-push-type"] == "alert"
    assert req.headers["apns-priority"] == "10"
    assert len(req.headers["apns-collapse-id"]) == 64  # обрезан до лимита
    assert req.headers["authorization"].startswith("bearer ")
    assert "apns-expiration" in req.headers

    body = json.loads(req.content)
    assert body["aps"]["alert"] == {"title": "Новая запись", "body": "Аружан — 02.09 10:00"}
    assert body["aps"]["badge"] == 3
    assert body["aps"]["thread-id"] == "day:2026-09-02"
    assert body["aps"]["interruption-level"] == "time-sensitive"
    assert body["type"] == "appointment.created"  # data рядом с aps


async def test_apns_dead_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(410, json={"reason": "Unregistered"})

    with pytest.raises(DeadTokenError):
        await _apns_channel(handler).send("gone", _message())


# ── Фабрика ─────────────────────────────────────────────────────────────────


async def test_factory_builds_live_channels_from_config(monkeypatch: Any) -> None:
    """Заполненные креды включают FCM/APNs; без них — лог-фолбэк."""
    build_push_transport.cache_clear()
    monkeypatch.setattr(settings, "push_enabled", True)
    monkeypatch.setattr(settings, "fcm_service_account_json", _service_account_json())
    monkeypatch.setattr(settings, "apns_key", _ec_key_pem())
    monkeypatch.setattr(settings, "apns_key_id", "KEYID12345")
    monkeypatch.setattr(settings, "apns_team_id", "TEAM123456")
    monkeypatch.setattr(settings, "apns_bundle_id", "kg.skrezo.app")
    transport = build_push_transport()
    assert transport._channels["android"].name == "fcm"
    assert transport._channels["ios"].name == "apns"

    build_push_transport.cache_clear()
    monkeypatch.setattr(settings, "push_enabled", False)
    transport = build_push_transport()
    assert transport._channels == {}
    build_push_transport.cache_clear()
