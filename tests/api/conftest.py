"""Общие фикстуры контура ассистента для tests/api.

Клиника с привязанным номером WhatsApp и владельцем, программируемый фейк
модели, помощники симулятора и патч фоновых задач — всё, что нужно и тестам
конвейера, и тестам заданий воркера. Живут здесь, а не в одном из тестовых
модулей: pytest раздаёт фикстуры из conftest без импорта, и соседний модуль
не спотыкается о «переопределение» имени фикстуры параметром теста.
"""

from __future__ import annotations

import itertools
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.main import app
from app.modules.ai.presentation.deps import get_ai_provider
from app.shared.ai import AiMessagePart, AiResult, AiToolDecl
from tests.conftest import API, Resources, otp_login

_phone_seq = itertools.count(1)


def _phone() -> str:
    return f"070080{next(_phone_seq):04d}"


def _reply_json(
    reply: str,
    *,
    lang: str = "ru",
    intent: str = "info",
    facts: list[str] | None = None,
    confidence: str = "high",
    needs_human: bool = False,
) -> str:
    return json.dumps(
        {
            "reply": reply,
            "lang": lang,
            "intent": intent,
            "used_facts": facts or [],
            "confidence": confidence,
            "needs_human": needs_human,
            "human_reason": None,
        },
        ensure_ascii=False,
    )


@dataclass
class _FakeProvider:
    """Отдаёт заранее заданный сценарий ходов; записывает, что получил."""

    script: list[AiResult]
    model_name: str = "fake-model"
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self,
        *,
        system: str,
        parts: list[AiMessagePart],
        tools: list[AiToolDecl],
        temperature: float,
        max_tokens: int,
    ) -> AiResult:
        self.calls.append({"system": system, "parts": parts})
        if not self.script:
            return AiResult(text=_reply_json("Хорошо."), model=self.model_name)
        return self.script.pop(0)


@dataclass(slots=True)
class Ctx:
    clinic_id: str
    owner_headers: dict[str, str]
    phone_number_id: str


@pytest_asyncio.fixture
async def fake() -> AsyncIterator[_FakeProvider]:
    provider = _FakeProvider(script=[])
    app.dependency_overrides[get_ai_provider] = lambda: provider
    yield provider
    app.dependency_overrides.pop(get_ai_provider, None)


@pytest.fixture
async def ctx(admin_client: AsyncClient, client: AsyncClient) -> Ctx:
    city = (await admin_client.get(f"{API}/admin/refs/cities")).json()[0]["id"]
    phone = _phone()
    created = await admin_client.post(
        f"{API}/admin/clients",
        json={
            "type": "clinic",
            "adminFullName": "Владелец ИИ",
            "adminPhone": phone,
            "name": "Клиника ИИ",
            "cityId": city,
            "businessPhone": "0700000000",
            "seats": 5,
        },
    )
    assert created.status_code == 201, created.text
    clinic_id = str(created.json()["id"])
    phone_number_id = f"9000{next(_phone_seq):06d}"
    bound = await admin_client.put(
        f"{API}/admin/clients/{clinic_id}/whatsapp",
        json={"phoneNumberId": phone_number_id, "displayPhone": "+996700999222"},
    )
    assert bound.status_code == 200, bound.text
    headers = await otp_login(client, phone)
    service = await client.post(
        f"{API}/clinic/services",
        headers=headers,
        json={"name": "Чистка", "priceTiyin": 250000},
    )
    assert service.status_code == 201, service.text
    return Ctx(clinic_id=clinic_id, owner_headers=headers, phone_number_id=phone_number_id)


async def _set_mode(admin_client: AsyncClient, ctx: Ctx, mode: str) -> None:
    resp = await admin_client.put(
        f"{API}/admin/clients/{ctx.clinic_id}/ai-mode", json={"mode": mode}
    )
    assert resp.status_code == 200, resp.text


async def _simulate(admin_client: AsyncClient, ctx: Ctx, text: str, phone: str) -> Any:
    resp = await admin_client.post(
        f"{API}/admin/ai/simulate-inbound",
        json={"clinicId": ctx.clinic_id, "fromPhone": phone, "text": text},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _patch_job(
    monkeypatch: pytest.MonkeyPatch, module: Any, provider: _FakeProvider, res: Resources
) -> None:
    """Фоновая задача живёт вне приложения: у неё своя сессия и свой провайдер."""
    monkeypatch.setattr(module, "session_factory", res.factory)
    monkeypatch.setattr(module, "build_ai_provider", lambda *_a, **_k: provider)
    monkeypatch.setattr(
        "app.modules.jobs.application.services.job_recorder.session_factory", res.factory
    )


def _inbound(
    phone_number_id: str, from_phone: str, text: str = "", *, audio_id: str | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "from": from_phone.lstrip("+"),
        "id": f"wamid.C{uuid.uuid4().hex}",
        "timestamp": str(int(time.time())),
    }
    if audio_id:
        message["type"] = "audio"
        message["audio"] = {"id": audio_id, "mime_type": "audio/ogg; codecs=opus", "voice": True}
    else:
        message["type"] = "text"
        message["text"] = {"body": text}
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
                            "messages": [message],
                        },
                    }
                ]
            }
        ],
    }


async def _webhook(client: AsyncClient, payload: dict[str, Any]) -> None:
    resp = await client.post(f"{API}/whatsapp/webhook", json=payload)
    assert resp.status_code == 200, resp.text
