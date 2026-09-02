"""Контракт AI-провайдера: один ход диалога с инструментами.

Провайдер получает системный промпт (кэшируемый префикс), историю хода и
описания инструментов; возвращает ЛИБО запрос на вызов инструмента (тогда
прикладной цикл исполняет его и продолжает ход), ЛИБО финальный текст —
строго JSON структурированного ответа (§5), парсит его прикладной слой.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class AiProviderError(Exception):
    """Провайдер недоступен или ответил мусором — ход не состоялся.

    Дальше по §14: один повтор, затем эскалация. Тишина лучше выдумки.
    """


@dataclass(frozen=True, slots=True)
class AiToolDecl:
    """Описание инструмента для модели (JSON-схема аргументов)."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema объектного типа


@dataclass(frozen=True, slots=True)
class AiMessagePart:
    """Одна реплика хода: пациент/бот/результат инструмента."""

    role: str  # user | model | tool
    text: str | None = None
    # role=model с вызовом инструмента:
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    # Подпись рассуждения (Gemini 3.x требует вернуть её вместе с functionCall
    # в истории хода — без неё API отвечает 400 thought_signature missing).
    tool_signature: str | None = None
    # role=tool — результат исполнения:
    tool_result: dict[str, Any] | list[Any] | None = None


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """Модель просит вызвать инструмент."""

    name: str
    args: dict[str, Any]
    # Провайдерская подпись вызова — вернуть в истории как пришла.
    signature: str | None = None


@dataclass(frozen=True, slots=True)
class AiResult:
    """Результат одного вызова: либо текст (финал), либо вызов инструмента."""

    text: str | None = None
    tool_call: ToolCallRequest | None = None
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class AiProvider(Protocol):
    """Что умеет транспорт модели."""

    #: Имя модели — пишется в ``ai_message.model`` (по нему видно, кто породил ответ).
    model_name: str

    async def complete(
        self,
        *,
        system: str,
        parts: list[AiMessagePart],
        tools: list[AiToolDecl],
        temperature: float,
        max_tokens: int,
    ) -> AiResult:
        """Один вызов модели; ошибки транспорта — :class:`AiProviderError`."""
        ...
