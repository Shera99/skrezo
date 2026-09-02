"""Сборка AI-провайдера из настроек; без ключа ассистент выключен."""

from __future__ import annotations

from app.core.config import settings
from app.shared.ai.gemini import GeminiProvider
from app.shared.ai.provider import AiProvider


def build_ai_provider() -> AiProvider | None:
    """Провайдер по конфигу; ``None`` — ассистент не настроен (пайплайн молчит).

    ``None`` вместо лог-фолбэка намеренно: «фейковый ответ пациенту» не бывает
    безвредным, в отличие от пуша в лог. Нет провайдера — диалог просто ждёт
    человека, как до Ф2.
    """
    if settings.ai_provider == "gemini" and settings.gemini_api_key:
        return GeminiProvider(
            settings.gemini_api_key,
            settings.gemini_model,
            thinking_level=settings.gemini_thinking_level,
        )
    return None
