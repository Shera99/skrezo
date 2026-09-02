"""Транспорт AI-провайдеров (§4.3 ТЗ ассистента).

Смена модели или провайдера — переменная окружения, не правка кода: выбор
модели решается языковым тестом, а лидер рынка меняется каждые месяцы.
Прикладной код зависит только от протокола :class:`AiProvider`.
"""

from __future__ import annotations

from app.shared.ai.provider import (
    AiMessagePart,
    AiProvider,
    AiProviderError,
    AiResult,
    AiToolDecl,
    ToolCallRequest,
)

__all__ = [
    "AiMessagePart",
    "AiProvider",
    "AiProviderError",
    "AiResult",
    "AiToolDecl",
    "ToolCallRequest",
]
