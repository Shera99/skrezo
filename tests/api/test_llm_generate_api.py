"""API-тесты ``POST /clinic/llm/generate`` — прокси голосового помощника приложения.

Ключевые риски:

* ключ Gemini не должен превратиться в бесплатный чат-бот: тело проверяется
  ДО Google (задача, поля, части, инструменты, размер), вложения не проходят;
* ``tools`` и ``generationConfig`` обязаны дойти до Google, иначе агент не
  вызовет инструмент, а классификатор вернёт прозу вместо JSON;
* модель, длину ответа и «размышления» задаёт сервер, а не приложение;
* лимиты (сотрудник/клиника) и квота Google — 429 с ``retryAfter`` и
  заголовком ``Retry-After``; сбой провайдера — 503 и предохранитель;
* режим чтения подписки — 403 ``subscription_inactive``;
* токены каждого обращения — в учёт клиники (``voice_*``).

Google подменён ``httpx.MockTransport``: видно, что ушло, и задаётся ответ.
"""

from __future__ import annotations

import itertools
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.core.config import settings
from app.main import app
from app.modules.access.application.services.subscription_policy import CLINIC_STATE_CACHE
from app.modules.llm.infrastructure.gemini_client import GeminiGenerateClient
from app.modules.llm.presentation.deps import get_llm_client
from app.shared.db.enums import SubscriptionStatus
from app.shared.db.models.clinic_usage_day import ClinicUsageDay
from app.shared.db.models.subscription import Subscription
from tests.api.conftest import Ctx
from tests.conftest import API, Resources, otp_login

URL = f"{API}/clinic/llm/generate"
_seq = itertools.count(1)

_GEMINI_OK: dict[str, Any] = {
    "candidates": [
        {
            "content": {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {"name": "day_overview", "args": {}},
                        "thoughtSignature": "CiQBVKhc7sig",
                    }
                ],
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 812, "candidatesTokenCount": 9, "thoughtsTokenCount": 3},
    "modelVersion": "gemini-test",
}


def _agent_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "task": "voice_agent",
        "contents": [
            {"role": "user", "parts": [{"text": 'Вопрос врача: "сколько сегодня записей"'}]}
        ],
        "systemInstruction": {"parts": [{"text": "Ты голосовой помощник врача."}]},
        "tools": [
            {
                "functionDeclarations": [
                    {"name": "day_overview", "description": "Обзор дня"},
                    {
                        "name": "free_slots",
                        "description": "Свободное время",
                        "parameters": {"type": "object", "properties": {}},
                    },
                ]
            }
        ],
    }
    body.update(overrides)
    return body


def _classify_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "task": "voice_classify",
        "contents": [{"parts": [{"text": 'Классифицируй: "пришёл"'}]}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    body.update(overrides)
    return body


@dataclass
class _Gemini:
    """Поддельный Google: отдаёт ответы по очереди и запоминает запросы."""

    responses: list[httpx.Response | type[Exception]] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0) if self.responses else httpx.Response(200, json=_GEMINI_OK)
        if isinstance(item, type):
            raise item("сбой", request=request)  # type: ignore[call-arg]
        return item

    def sent(self, index: int = -1) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(self.requests[index].content)
        return body


@pytest.fixture
def gemini(_resources: Resources) -> _Gemini:
    fake = _Gemini()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    app.dependency_overrides[get_llm_client] = lambda: GeminiGenerateClient("test-key", client)
    return fake


async def _usage(res: Resources, clinic_id: str) -> ClinicUsageDay | None:
    async with res.factory() as session:
        return (
            await session.execute(
                select(ClinicUsageDay).where(ClinicUsageDay.clinic_id == uuid.UUID(clinic_id))
            )
        ).scalar_one_or_none()


# ── 1. Насквозь: тело, модель, ответ, учёт ──────────────────────────────────


async def test_agent_request_goes_through_with_server_limits_and_usage(
    client: AsyncClient,
    ctx: Ctx,
    gemini: _Gemini,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tools и systemInstruction доходят до Google; модель и потолки — серверные."""
    monkeypatch.setattr(settings, "llm_app_model_agent", "gemini-agent-test")
    body = _agent_body(generationConfig={"temperature": 0.4, "maxOutputTokens": 9999, "topK": 5})

    resp = await client.post(URL, headers=ctx.owner_headers, json=body)

    assert resp.status_code == 200, resp.text
    assert resp.json() == _GEMINI_OK  # ответ Gemini без изменений
    request = gemini.requests[0]
    assert str(request.url).endswith("/models/gemini-agent-test:generateContent")
    assert request.headers["x-goog-api-key"] == "test-key"
    sent = gemini.sent()
    assert sent["contents"] == body["contents"]
    assert sent["systemInstruction"] == body["systemInstruction"]
    assert sent["tools"] == body["tools"]
    assert "task" not in sent
    generation = sent["generationConfig"]
    assert generation["temperature"] == 0.4
    assert generation["maxOutputTokens"] == settings.llm_app_max_output_tokens_agent
    assert generation["candidateCount"] == 1
    assert "topK" not in generation  # лишнее приложение не задаёт
    usage = await _usage(_resources, ctx.clinic_id)
    assert usage is not None
    assert (usage.voice_calls, usage.voice_input_tokens, usage.voice_output_tokens) == (1, 812, 12)
    assert usage.model_calls == 0  # ассистент пациентов — отдельно


async def test_classifier_keeps_json_mode_and_its_own_model(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без responseMimeType классификатор вернул бы прозу — режим обязан дойти."""
    monkeypatch.setattr(settings, "llm_app_model_classify", "gemini-lite-test")

    resp = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())

    assert resp.status_code == 200, resp.text
    assert str(gemini.requests[0].url).endswith("/models/gemini-lite-test:generateContent")
    generation = gemini.sent()["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert generation["maxOutputTokens"] == settings.llm_app_max_output_tokens_classify
    assert "tools" not in gemini.sent()


async def test_model_turn_with_thought_signature_is_accepted(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini
) -> None:
    """Цикл агента: ответ модели целиком (с thoughtSignature) и результат функции."""
    model_turn = _GEMINI_OK["candidates"][0]["content"]
    contents = [
        *_agent_body()["contents"],
        model_turn,
        {
            "role": "user",
            "parts": [{"functionResponse": {"name": "day_overview", "response": {"count": 5}}}],
        },
    ]

    resp = await client.post(URL, headers=ctx.owner_headers, json=_agent_body(contents=contents))

    assert resp.status_code == 200, resp.text
    assert gemini.sent()["contents"][1] == model_turn


async def test_any_clinic_staff_member_may_use_it(
    admin_client: AsyncClient, client: AsyncClient, ctx: Ctx, gemini: _Gemini
) -> None:
    """Отдельного права нет: врач клиники пользуется помощником как владелец."""
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]
    phone = f"070071{next(_seq):04d}"
    added = await client.post(
        f"{API}/clinic/staff",
        headers=ctx.owner_headers,
        json={"fullName": "Врач Помощника", "phone": phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    doctor = await otp_login(client, phone)

    resp = await client.post(URL, headers=doctor, json=_classify_body())

    assert resp.status_code == 200, resp.text


async def test_requires_login(client: AsyncClient, gemini: _Gemini) -> None:
    resp = await client.post(URL, json=_classify_body())

    assert resp.status_code == 401
    assert gemini.requests == []


# ── 2. Контракт тела: 422 до Google ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (_agent_body(task="chat"), "task"),
        (_agent_body(model="gemini-pro"), "model"),  # модель выбирает сервер
        (
            _agent_body(
                contents=[
                    {
                        "role": "user",
                        "parts": [{"inlineData": {"mimeType": "audio/ogg", "data": "AAAA"}}],
                    }
                ]
            ),
            "inlineData",  # звук распознаётся на устройстве
        ),
        (_agent_body(contents=[{"parts": [{"text": "x"}]}] * 13), "не больше 12"),
        (_agent_body(tools=[{"googleSearch": {}}]), "functionDeclarations"),
        (_classify_body(tools=_agent_body()["tools"]), "voice_classify"),
        (_agent_body(contents=[]), "contents"),
        (
            _agent_body(systemInstruction={"parts": [{"functionCall": {"name": "x"}}]}),
            "systemInstruction",
        ),
    ],
)
async def test_contract_violations_are_rejected_before_google(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini, body: dict[str, Any], fragment: str
) -> None:
    resp = await client.post(URL, headers=ctx.owner_headers, json=body)

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "llm_request_invalid"
    assert fragment in resp.json()["message"]
    assert gemini.requests == []


async def test_too_many_functions_are_rejected(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini
) -> None:
    tools = [{"functionDeclarations": [{"name": f"f{i}"} for i in range(17)]}]

    resp = await client.post(URL, headers=ctx.owner_headers, json=_agent_body(tools=tools))

    assert resp.status_code == 422
    assert "16" in resp.json()["message"]


async def test_not_json_and_oversized_bodies_are_rejected(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = await client.post(
        URL,
        headers={**ctx.owner_headers, "Content-Type": "application/json"},
        content=b"{not json",
    )
    assert broken.status_code == 422
    assert broken.json()["error"] == "llm_request_invalid"

    monkeypatch.setattr(settings, "llm_app_max_body_bytes", 200)
    big = await client.post(URL, headers=ctx.owner_headers, json=_agent_body())
    assert big.status_code == 422
    assert "байт" in big.json()["message"]
    assert gemini.requests == []


# ── 3. Ответы Google ────────────────────────────────────────────────────────


async def test_google_rejecting_the_body_is_422_with_its_reason(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini
) -> None:
    """Чаще всего — пересобранный без thoughtSignature ответ модели в цикле."""
    gemini.responses = [
        httpx.Response(
            400,
            json={
                "error": {
                    "code": 400,
                    "message": "Function call is missing a thought_signature",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )
    ]

    resp = await client.post(URL, headers=ctx.owner_headers, json=_agent_body())

    assert resp.status_code == 422
    assert resp.json()["error"] == "llm_request_rejected"
    assert "thought_signature" in resp.json()["message"]


async def test_google_quota_is_429_with_retry_after(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini
) -> None:
    gemini.responses = [httpx.Response(429, headers={"Retry-After": "30"}, json={})]

    resp = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())

    assert resp.status_code == 429
    assert resp.json()["error"] == "llm_upstream_busy"
    assert resp.json()["retryAfter"] == 30
    assert resp.headers["Retry-After"] == "30"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": {"message": "internal"}}),
        httpx.ReadTimeout,
        httpx.Response(
            400,
            json={
                "error": {
                    "code": 400,
                    "message": "API key not valid. Please pass a valid API key.",
                    "status": "INVALID_ARGUMENT",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "API_KEY_INVALID",
                            "domain": "googleapis.com",
                        }
                    ],
                }
            },
        ),
        httpx.Response(
            400,
            json={
                "error": {
                    "code": 400,
                    "message": "User location is not supported for the API use.",
                    "status": "FAILED_PRECONDITION",
                }
            },
        ),
    ],
    ids=["server_error", "timeout", "bad_key", "region"],
)
async def test_google_failure_is_503_unavailable(
    client: AsyncClient,
    ctx: Ctx,
    gemini: _Gemini,
    response: httpx.Response | type[Exception],
) -> None:
    """Сбой, таймаут, неверный ключ, регион — «недоступно», а не ошибка тела приложения."""
    gemini.responses = [response]

    resp = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())

    assert resp.status_code == 503, resp.text
    assert resp.json()["error"] == "llm_unavailable"


async def test_breaker_opens_after_series_of_failures(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Серия сбоев размыкает цепь: дальше 503 сразу, без похода в Google."""
    monkeypatch.setattr(settings, "ai_breaker_threshold", 2)
    gemini.responses = [httpx.Response(503, json={}), httpx.Response(503, json={})]

    for _ in range(2):
        failed = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())
        assert failed.status_code == 503
    blocked = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())

    assert blocked.status_code == 503
    assert blocked.json()["error"] == "llm_unavailable"
    assert len(gemini.requests) == 2  # третий запрос до Google не дошёл


async def test_not_configured_is_503_disabled(client: AsyncClient, ctx: Ctx) -> None:
    app.dependency_overrides[get_llm_client] = lambda: None

    resp = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())

    assert resp.status_code == 503
    assert resp.json()["error"] == "llm_disabled"


# ── 4. Лимиты ───────────────────────────────────────────────────────────────


async def test_staff_minute_limit_is_429_with_retry_after(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "llm_app_limit_per_minute", 2)

    for _ in range(2):
        ok = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())
        assert ok.status_code == 200
    limited = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())

    assert limited.status_code == 429
    body = limited.json()
    assert body["error"] == "llm_quota_exceeded"
    assert 0 < body["retryAfter"] <= 60
    assert limited.headers["Retry-After"] == str(body["retryAfter"])
    assert len(gemini.requests) == 2  # сверх лимита Google не зовётся


async def test_clinic_daily_limit_applies_to_all_staff(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "llm_app_clinic_limit_per_day", 1)

    first = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())
    second = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"] == "llm_quota_exceeded"
    assert second.json()["retryAfter"] > 3600  # окно — сутки


async def test_invalid_request_does_not_spend_the_limit(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Неверное тело отклоняется до лимита — счётчик не тратится."""
    monkeypatch.setattr(settings, "llm_app_limit_per_minute", 1)

    bad = await client.post(URL, headers=ctx.owner_headers, json=_agent_body(task="chat"))
    good = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())

    assert bad.status_code == 422
    assert good.status_code == 200


# ── 5. Подписка ─────────────────────────────────────────────────────────────


async def test_read_only_subscription_is_403(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini, _resources: Resources
) -> None:
    """Кончился триал — помощник недоступен: каждый вопрос стоит денег."""
    async with _resources.factory() as session:
        await session.execute(
            update(Subscription)
            .where(Subscription.clinic_id == uuid.UUID(ctx.clinic_id))
            .values(
                status=SubscriptionStatus.TRIAL,
                trial_ends_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await session.commit()
    await _resources.fake.delete(CLINIC_STATE_CACHE.format(ctx.clinic_id))

    resp = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())

    assert resp.status_code == 403
    assert resp.json()["error"] == "subscription_inactive"
    assert gemini.requests == []


# ── 6. Находки ревью: обход контракта и общий предохранитель ────────────────


async def test_echoed_field_name_api_key_does_not_trip_the_breaker(
    client: AsyncClient,
    ctx: Ctx,
    gemini: _Gemini,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google повторяет имена полей тела: «API_KEY» в теле — не неверный ключ.

    Иначе сотрудник любой клиники пятью такими запросами размыкал бы
    предохранитель и выключал помощника всем клиникам.
    """
    monkeypatch.setattr(settings, "ai_breaker_threshold", 2)
    echo = httpx.Response(
        400,
        json={
            "error": {
                "code": 400,
                "message": "Invalid JSON payload. Unknown name \"API_KEY\" at 'contents[0]'",
                "status": "INVALID_ARGUMENT",
            }
        },
    )
    gemini.responses = [echo, echo, echo]

    for _ in range(3):
        resp = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())
        assert resp.status_code == 422
        assert resp.json()["error"] == "llm_request_rejected"

    assert len(gemini.requests) == 3  # цепь не разомкнута — все запросы дошли
    assert await _resources.fake.get("llm:app:failures") is None
    assert await _resources.fake.exists("llm:app:open") == 0


async def test_google_quota_does_not_trip_the_breaker_and_reads_retry_delay(
    client: AsyncClient,
    ctx: Ctx,
    gemini: _Gemini,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Квота Google — честный 429 со сроком из RetryInfo, а не 503 для всех клиник."""
    monkeypatch.setattr(settings, "ai_breaker_threshold", 2)
    busy = httpx.Response(
        429,
        json={
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": "Quota exceeded",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}
                ],
            }
        },
    )
    gemini.responses = [busy, busy, busy]

    for _ in range(3):
        resp = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())
        assert resp.status_code == 429
        assert resp.json()["error"] == "llm_upstream_busy"
        assert resp.json()["retryAfter"] == 37

    assert await _resources.fake.exists("llm:app:open") == 0


async def test_breakers_of_voice_and_patient_assistants_are_separate(
    client: AsyncClient,
    ctx: Ctx,
    gemini: _Gemini,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Разомкнутый предохранитель ассистента пациентов не глушит голосового, и наоборот."""
    await _resources.fake.set("ai:provider:open", "1", ex=60)
    works = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())
    assert works.status_code == 200

    monkeypatch.setattr(settings, "ai_breaker_threshold", 1)
    gemini.responses = [httpx.Response(503, json={})]
    await client.post(URL, headers=ctx.owner_headers, json=_classify_body())
    assert await _resources.fake.exists("llm:app:open") == 1
    await _resources.fake.delete("ai:provider:open")
    assert await _resources.fake.exists("ai:provider:open") == 0


@pytest.mark.parametrize(
    ("part", "fragment"),
    [
        (
            {
                "functionResponse": {
                    "name": "photo",
                    "response": {},
                    "parts": [{"inlineData": {"mimeType": "image/jpeg", "data": "AAAA"}}],
                }
            },
            "parts",  # Gemini 3 принимает вложения внутри ответа функции
        ),
        ({"functionCall": {"name": "x", "args": {}, "API_KEY": 1}}, "API_KEY"),
        ({"functionCall": {"args": {}}}, "name"),
        ({"functionResponse": {"name": "x", "response": "текст"}}, "response"),
        ({"text": "ok", "thoughtSignature": 123}, "thoughtSignature"),
    ],
)
async def test_function_parts_are_checked_inside(
    client: AsyncClient, ctx: Ctx, gemini: _Gemini, part: dict[str, Any], fragment: str
) -> None:
    contents = [{"role": "user", "parts": [part]}]

    resp = await client.post(URL, headers=ctx.owner_headers, json=_agent_body(contents=contents))

    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "llm_request_invalid"
    assert fragment in resp.json()["message"]
    assert gemini.requests == []


async def test_clinic_daily_limit_is_shared_by_staff(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    gemini: _Gemini,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Суточный лимит — на клинику: второй сотрудник упирается в лимит, потраченный первым."""
    spec = (await admin_client.get(f"{API}/admin/refs/specializations")).json()[0]["id"]
    phone = f"070071{next(_seq):04d}"
    added = await client.post(
        f"{API}/clinic/staff",
        headers=ctx.owner_headers,
        json={"fullName": "Второй Врач", "phone": phone, "specializationId": spec},
    )
    assert added.status_code == 201, added.text
    doctor = await otp_login(client, phone)
    monkeypatch.setattr(settings, "llm_app_clinic_limit_per_day", 1)

    owner = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())
    other = await client.post(URL, headers=doctor, json=_classify_body())

    assert owner.status_code == 200
    assert other.status_code == 429
    assert other.json()["error"] == "llm_quota_exceeded"


async def test_refused_request_does_not_spend_staff_windows(
    client: AsyncClient,
    ctx: Ctx,
    gemini: _Gemini,
    _resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Отказ по суткам клиники не съедает минутный лимит сотрудника."""
    monkeypatch.setattr(settings, "llm_app_clinic_limit_per_day", 1)
    assert (await client.post(URL, headers=ctx.owner_headers, json=_classify_body())).is_success
    for _ in range(3):
        refused = await client.post(URL, headers=ctx.owner_headers, json=_classify_body())
        assert refused.status_code == 429

    keys = await _resources.fake.keys("rl:llm:account:minute:*")
    assert len(keys) == 1
    assert await _resources.fake.get(keys[0]) == "1"  # только состоявшееся обращение
    assert await _resources.fake.ttl(keys[0]) > 0  # окно всегда со сроком


async def test_voice_usage_reaches_admin_report_and_cost(
    admin_client: AsyncClient,
    client: AsyncClient,
    ctx: Ctx,
    gemini: _Gemini,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Голосовые счётчики видны команде Skrezo и входят в смету по ставкам модели."""
    monkeypatch.setattr(settings, "ai_rate_input_tiyin_per_mtok", 1_000_000)
    monkeypatch.setattr(settings, "ai_rate_output_tiyin_per_mtok", 2_000_000)
    assert (await client.post(URL, headers=ctx.owner_headers, json=_agent_body())).is_success

    resp = await admin_client.get(f"{API}/admin/clients/{ctx.clinic_id}/usage")

    assert resp.status_code == 200, resp.text
    totals = resp.json()["totals"]
    assert (totals["voiceCalls"], totals["voiceInputTokens"], totals["voiceOutputTokens"]) == (
        1,
        812,
        12,
    )
    assert resp.json()["estimatedCostTiyin"] == 812 * 1 + 12 * 2
