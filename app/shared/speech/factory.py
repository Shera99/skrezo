"""Сборка провайдера распознавания; без настройки голосовые уходят человеку."""

from __future__ import annotations

from app.core.config import settings
from app.shared.speech.gemini import GeminiSpeechProvider
from app.shared.speech.provider import SpeechProvider


def build_speech_provider() -> SpeechProvider | None:
    """Провайдер по конфигу; ``None`` — распознавание выключено.

    Как и у модели, лог-заглушки нет: выдуманная расшифровка опаснее её
    отсутствия. Выключено — голосовое сохраняется и уходит администратору.
    """
    if settings.speech_provider == "gemini" and settings.gemini_api_key:
        return GeminiSpeechProvider(settings.gemini_api_key, settings.speech_model)
    return None
