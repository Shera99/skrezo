"""Провайдер Gemini (Google AI Studio, REST v1beta) — function calling.

Бесплатный тир годится для теневого режима и пилота (лимиты на минуту/день).
⚠️ На бесплатном тире Google может использовать содержимое запросов для
улучшения своих продуктов — ТЗ §12.1 требует режим без хранения, поэтому до
реальных пациентов нужен платный тир либо осознанное решение владельца.

Структурированный ответ: пока идут инструменты — обычные ходы; финальный
текст модель обязана отдать строго JSON (контракт в системном промпте),
парсит его прикладной слой. Одновременный JSON-mime с tools у Gemini
ограничен — поэтому контракт промптом, а не generation_config.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.shared.ai.provider import (
    AiMessagePart,
    AiProviderError,
    AiResult,
    AiToolDecl,
    ToolCallRequest,
)

logger = structlog.get_logger(__name__)

_TIMEOUT = 45.0


class GeminiProvider:
    """REST-клиент ``generateContent`` с function calling."""

    def __init__(
        self, api_key: str, model: str, client: httpx.AsyncClient | None = None
    ) -> None:
        self._api_key = api_key
        self.model_name = model
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT)

    def _url(self) -> str:
        return (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_name}:generateContent"
        )

    @staticmethod
    def _contents(parts: list[AiMessagePart]) -> list[dict[str, Any]]:
        contents: list[dict[str, Any]] = []
        for part in parts:
            if part.role == "tool":
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": part.tool_name or "tool",
                                    "response": {"result": part.tool_result},
                                }
                            }
                        ],
                    }
                )
            elif part.role == "model" and part.tool_name is not None:
                contents.append(
                    {
                        "role": "model",
                        "parts": [
                            {
                                "functionCall": {
                                    "name": part.tool_name,
                                    "args": part.tool_args or {},
                                }
                            }
                        ],
                    }
                )
            else:
                contents.append(
                    {
                        "role": "model" if part.role == "model" else "user",
                        "parts": [{"text": part.text or ""}],
                    }
                )
        return contents

    async def complete(
        self,
        *,
        system: str,
        parts: list[AiMessagePart],
        tools: list[AiToolDecl],
        temperature: float,
        max_tokens: int,
    ) -> AiResult:
        body: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": self._contents(parts),
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if tools:
            body["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in tools
                    ]
                }
            ]
        try:
            resp = await self._client.post(
                self._url(), json=body, headers={"x-goog-api-key": self._api_key}
            )
        except httpx.HTTPError as exc:
            raise AiProviderError(f"Gemini: {exc}") from exc
        if resp.status_code >= 400:
            logger.warning("gemini_failed", status=resp.status_code, body=resp.text[:400])
            raise AiProviderError(f"Gemini: HTTP {resp.status_code}")
        data = resp.json()
        try:
            candidate = data["candidates"][0]
            content_parts = candidate.get("content", {}).get("parts") or []
        except (KeyError, IndexError) as exc:
            raise AiProviderError("Gemini: пустой ответ") from exc

        usage_meta = data.get("usageMetadata") or {}
        usage = {
            "input": int(usage_meta.get("promptTokenCount") or 0),
            "output": int(usage_meta.get("candidatesTokenCount") or 0),
        }
        for item in content_parts:
            call = item.get("functionCall")
            if call:
                return AiResult(
                    tool_call=ToolCallRequest(
                        name=str(call.get("name") or ""), args=dict(call.get("args") or {})
                    ),
                    model=self.model_name,
                    usage=usage,
                )
        text = "".join(str(item.get("text") or "") for item in content_parts)
        if not text.strip():
            raise AiProviderError("Gemini: ответ без текста и без вызова инструмента")
        return AiResult(text=text, model=self.model_name, usage=usage)
