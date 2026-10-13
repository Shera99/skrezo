"""Схемы ручки голосового помощника — для документации OpenAPI.

Тело запроса сервер разбирает сам (:mod:`app.modules.llm.domain.request`): ему
нужны свои коды ошибок контракта (``llm_request_invalid``), а ответ — это
JSON Gemini без изменений. Поэтому модели ниже описывают формат в Swagger,
но не участвуют в валидации и не фильтруют ответ.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field

from app.shared.api.schemas import CamelModel

_AGENT_EXAMPLE: dict[str, Any] = {
    "task": "voice_agent",
    "contents": [
        {
            "role": "user",
            "parts": [
                {
                    "text": (
                        'Сегодня 2026-10-11, время 09:40.\nВопрос врача: "сколько сегодня записей"'
                    )
                }
            ],
        }
    ],
    "systemInstruction": {"parts": [{"text": "<системная инструкция приложения>"}]},
    "tools": [
        {
            "functionDeclarations": [
                {"name": "day_overview", "description": "Обзор дня врача"},
                {"name": "next_appointment", "description": "Ближайший приём"},
            ]
        }
    ],
}

_CLASSIFY_EXAMPLE: dict[str, Any] = {
    "task": "voice_classify",
    "contents": [{"parts": [{"text": '<промпт классификатора> "пришёл"'}]}],
    "generationConfig": {"responseMimeType": "application/json"},
}


class LlmGenerateRequestDoc(CamelModel):
    """Запрос приложения: задача и поля Gemini, которые проходят насквозь."""

    model_config = ConfigDict(json_schema_extra={"examples": [_AGENT_EXAMPLE, _CLASSIFY_EXAMPLE]})

    task: Literal["voice_agent", "voice_classify"] = Field(
        description=(
            "Задача: `voice_agent` — голосовой помощник с инструментами (цикл до 3 "
            "обращений на вопрос), `voice_classify` — фраза → JSON без инструментов. "
            "От задачи зависят модель, таймаут и потолок ответа"
        ),
        examples=["voice_agent"],
    )
    contents: list[dict[str, Any]] = Field(
        description=(
            "Сообщения Gemini (`role` user|model, `parts`), до 12. Части — только "
            "`text`, `functionCall`, `functionResponse` (+ `thoughtSignature`). В цикле "
            "агента ответ модели кладётся сюда целиком, с `thoughtSignature`"
        ),
    )
    system_instruction: dict[str, Any] | None = Field(
        default=None, description="Системная инструкция приложения: `parts` только с `text`"
    )
    tools: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Только `functionDeclarations`, до 16 функций; у `voice_classify` не передаётся"
        ),
    )
    generation_config: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Учитываются `responseMimeType`, `responseSchema`, `temperature`; длину "
            "ответа, число вариантов и «размышления» задаёт сервер"
        ),
        examples=[{"responseMimeType": "application/json"}],
    )


class LlmGenerateResponseDoc(CamelModel):
    """Ответ Gemini ``generateContent`` без изменений (поля ниже — главное)."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "examples": [
                {
                    "candidates": [
                        {
                            "content": {
                                "role": "model",
                                "parts": [
                                    {
                                        "functionCall": {"name": "day_overview", "args": {}},
                                        "thoughtSignature": "CiQBVKhc7…",
                                    }
                                ],
                            },
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"promptTokenCount": 812, "candidatesTokenCount": 9},
                    "modelVersion": "gemini-3.6-flash",
                }
            ]
        },
    )

    candidates: list[dict[str, Any]] = Field(
        description="Варианты ответа (всегда один): `content.parts` — текст или вызов функции"
    )
    usage_metadata: dict[str, Any] | None = Field(
        default=None, description="Токены запроса и ответа — сервер учитывает их на клинику"
    )
    model_version: str | None = Field(default=None, description="Модель, выбранная сервером")


def _error(description: str, error: str, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"example": {"error": error, "message": message, **extra}}},
    }


GENERATE_ERRORS: dict[int | str, dict[str, Any]] = {
    200: {"model": LlmGenerateResponseDoc, "description": "Ответ Gemini как есть"},
    403: _error(
        "Подписка клиники в режиме чтения (кончился триал или оплата) или сотрудника "
        "убрали из клиники (`membership_revoked`)",
        "subscription_inactive",
        "Подписка неактивна: доступно только чтение — продлите доступ",
    ),
    422: _error(
        "Тело не по контракту (`llm_request_invalid`: задача, поля, части, инструменты, "
        "размер) или Google не принял тело (`llm_request_rejected`: чаще всего ответ "
        "модели в цикле пересобран без `thoughtSignature`)",
        "llm_request_invalid",
        "contents[0].parts[0]: недопустимые поля inlineData — разрешены functionCall, "
        "functionResponse, text, thought, thoughtSignature",
    ),
    429: {
        "description": (
            "Лимит: наш (`llm_quota_exceeded` — 20 обращений в минуту и 150 в час на "
            "сотрудника, 1500 в сутки на клинику) или квота ключа у Google "
            "(`llm_upstream_busy`). `retryAfter` — через сколько секунд повторить; тот же "
            "срок в заголовке `Retry-After`"
        ),
        "headers": {
            "Retry-After": {
                "description": "Через сколько секунд повторить",
                "schema": {"type": "integer"},
            }
        },
        "content": {
            "application/json": {
                "example": {
                    "error": "llm_quota_exceeded",
                    "message": "Лимит запросов к ассистенту исчерпан",
                    "retryAfter": 120,
                }
            }
        },
    },
    503: _error(
        "Модель не ответила вовремя или недоступна (`llm_unavailable`), либо помощник не "
        "настроен на сервере (`llm_disabled`)",
        "llm_unavailable",
        "Ассистент временно недоступен, попробуйте позже",
    ),
}

#: Тело запроса в OpenAPI: разбирает его сервер сам, поэтому схема — отдельно.
GENERATE_REQUEST_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": LlmGenerateRequestDoc.model_json_schema(by_alias=True),
                "examples": {
                    "voice_agent": {"summary": "Голосовой помощник", "value": _AGENT_EXAMPLE},
                    "voice_classify": {
                        "summary": "Классификатор фразы",
                        "value": _CLASSIFY_EXAMPLE,
                    },
                },
            }
        },
    }
}
